"""PRD approvers analysis — the inventory's `prd_approvers` variable.

Each project's group_vars/all may define `prd_approvers` (a list of usernames
— YAML list or comma string, both accepted). This module builds the Access
page's approvers section:

  * per-USER stats — how many / which projects and prd_teams each username
    approves for
  * per-TEAM view — for every prd_team, the approvers COMMON to all of the
    team's projects vs. the ones only some projects carry
  * anomalies:
      - outside_ldap      approver not a member of the prd_team's LDAP group
                          (resolved with the SAME Engine-script mechanism the
                          Azure access page uses)
      - inconsistent_team same prd_team, different approver lists per project
      - no_approvers      project HAS a prd_team but defines no approvers
      - single_approver   only one approver (bus factor)
      - duplicate_entry   the same username repeated inside one list
      - app_override      an app's group_vars overrides the project's list
"""

import re
import time

from ..config import settings  # noqa: F401 — parity with sibling integrations

_CACHE: dict = {"at": 0.0, "payload": None}
_TTL = 300


def _clean(u) -> str:
    """One approver entry as written → canonical form: stray list brackets /
    braces (from raw '["x", "y"]' strings), surrounding quotes and whitespace
    stripped, lower-cased (usernames are case-insensitive)."""
    s = str(u or "").strip()
    s = s.strip("[](){}").strip()   # '["alice"' / '"bob"]' → '"alice"' / '"bob"'
    return s.strip("'\"").strip().lower()


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        vals = v.replace(";", ",").split(",")
    elif isinstance(v, (list, tuple, set)):
        vals = v
    else:
        return []
    return [c for c in (_clean(x) for x in vals) if c]


def _norm(u) -> str:
    return _clean(u)


def _ukey(u) -> str:
    """MATCHING key: quotes/case/separator/dot/bracket-insensitive —
    "A.Meshhal", 'a.meshhal', '["A_Meshhal"]' and 'ameshhal' → 'ameshhal'."""
    return re.sub(r"[\s._\-'\"\[\](){}]+", "", _clean(u))


def _ldap_lookup(team: str) -> dict:
    """Resolve a team's LDAP group members — same resolver as the Azure access
    page (Engine repo getTeamMembersCN.sh, 1h-cached), trying separator
    variants because inventory teams are underscored while CNs are dashed."""
    from ..auth import ldap_group_members
    tried: list[str] = []
    for cand in (team, team.replace("_", "-"), team.replace(" ", "-"),
                 team.replace(" ", "_"), team.replace("-", "_")):
        if cand.lower() in (t.lower() for t in tried):
            continue
        tried.append(cand)
        res = ldap_group_members(cand)
        if res.get("found"):
            return {"found": True, "group": cand,
                    "members": res.get("members") or [], "tried": tried}
    return {"found": False, "group": team, "members": [], "tried": tried}


def _member_keys(members: list[dict]) -> set:
    """Every way an approver entry may denote a group member. prd_approvers
    hold sAMAccountNames while getTeamMembersCN.sh may return CNs (display
    names), so besides the username and display name we derive the COMMON
    sAMAccountName conventions from each display name — first.last, f.last,
    firstlast, last.f — all through the separator/dot-insensitive _ukey."""
    keys = set()
    for m in members or []:
        username = m.get("username") or ""
        display = m.get("display_name") or ""
        for v in (username, display):
            if v:
                keys.add(_ukey(v))
        parts = [p for p in re.split(r"[\s._\-]+", _clean(display)) if p]
        if parts:
            first, last = parts[0], parts[-1]
            keys.add(_ukey(first))                       # jdoe styles: first only
            if len(parts) > 1:
                keys.add(_ukey(first + last))            # first.last / firstlast
                keys.add(_ukey(first[0] + last))         # j.doe / jdoe
                keys.add(_ukey(last + first[0]))         # doej
                keys.add(_ukey(first + last[0]))         # johnd
    keys.discard("")
    return keys


def analyze(refresh: bool = False) -> dict:
    if not refresh and _CACHE["payload"] and time.time() - _CACHE["at"] < _TTL:
        return {**_CACHE["payload"], "cached": True}
    payload = _analyze()
    _CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}


def invalidate() -> None:
    _CACHE.update(at=0.0, payload=None)


def _analyze() -> dict:
    from . import inventory
    inv = inventory.parse()

    projects_out = []
    anomalies: list[dict] = []
    for p in inv.get("projects") or []:
        cfg = p.get("config") or {}
        pv = cfg.get("project_vars") or {}
        approvers = _as_list(pv.get("prd_approvers"))
        app_overrides = {}
        for app, av in (cfg.get("app_vars") or {}).items():
            if isinstance(av, dict) and "prd_approvers" in av:
                lst = _as_list(av.get("prd_approvers"))
                if sorted(map(_norm, lst)) != sorted(map(_norm, approvers)):
                    app_overrides[app] = lst
        pr = {"project": p["name"], "prd_team": p.get("prd_team"),
              "approvers": approvers, "app_overrides": app_overrides}
        projects_out.append(pr)

        # per-project anomalies
        uniq = []
        for a in approvers:
            if _norm(a) in map(_norm, uniq):
                anomalies.append({"kind": "duplicate_entry", "project": p["name"],
                                  "user": a,
                                  "detail": f"'{a}' appears more than once in "
                                            f"{p['name']}'s prd_approvers"})
            else:
                uniq.append(a)
        if pr["prd_team"] and not approvers:
            anomalies.append({"kind": "no_approvers", "project": p["name"],
                              "team": pr["prd_team"],
                              "detail": f"{p['name']} has prd_team "
                                        f"{pr['prd_team']} but defines no "
                                        f"prd_approvers — nobody can approve"})
        if len(uniq) == 1 and pr["prd_team"]:
            anomalies.append({"kind": "single_approver", "project": p["name"],
                              "user": uniq[0],
                              "detail": f"{p['name']} has a single approver "
                                        f"('{uniq[0]}') — no cover when absent"})
        for app, lst in app_overrides.items():
            anomalies.append({"kind": "app_override", "project": p["name"],
                              "detail": f"app '{app}' overrides {p['name']}'s "
                                        f"prd_approvers with "
                                        f"[{', '.join(lst) or 'empty'}]"})

    # ---- per-TEAM: common approvers + LDAP membership + consistency -------
    by_team: dict = {}
    for pr in projects_out:
        if pr["prd_team"]:
            by_team.setdefault(pr["prd_team"], []).append(pr)
    teams_out = []
    for team in sorted(by_team):
        prs = by_team[team]
        lists = {pr["project"]: sorted({_norm(a) for a in pr["approvers"]})
                 for pr in prs}
        with_lists = {k: v for k, v in lists.items() if v}
        union = sorted(set().union(*with_lists.values())) if with_lists else []
        common = sorted(set.intersection(*(set(v) for v in with_lists.values()))) \
            if with_lists else []
        consistent = len({tuple(v) for v in with_lists.values()}) <= 1
        ldap = _ldap_lookup(team)
        keys = _member_keys(ldap["members"]) if ldap["found"] else set()
        outside = [u for u in union if ldap["found"] and _ukey(u) not in keys]
        if not consistent:
            diff = "; ".join(f"{proj}: [{', '.join(v) or '—'}]"
                             for proj, v in sorted(lists.items()))
            anomalies.append({"kind": "inconsistent_team", "team": team,
                              "detail": f"projects of prd_team {team} disagree "
                                        f"on approvers — {diff}"})
        for u in outside:
            anomalies.append({"kind": "outside_ldap", "team": team, "user": u,
                              "detail": f"'{u}' approves for {team} but is NOT "
                                        f"a member of the {ldap['group']} LDAP "
                                        f"group"})
        teams_out.append({"team": team,
                          "projects": sorted(lists),
                          "per_project": lists,
                          "common": common, "union": union,
                          "consistent": consistent,
                          "ldap_found": ldap["found"], "ldap_group": ldap["group"],
                          "ldap_tried": ldap.get("tried") or [],
                          "ldap_members": [m.get("display_name") or m.get("username")
                                           for m in ldap["members"]],
                          "outside_ldap": outside})

    # ---- per-USER stats ---------------------------------------------------
    users: dict = {}
    for pr in projects_out:
        for a in {_norm(x) for x in pr["approvers"]}:
            u = users.setdefault(a, {"username": a, "projects": [], "teams": set(),
                                     "outside": set()})
            u["projects"].append(pr["project"])
            if pr["prd_team"]:
                u["teams"].add(pr["prd_team"])
    for t in teams_out:
        for u in t["outside_ldap"]:
            if u in users:
                users[u]["outside"].add(t["team"])
    users_out = sorted(({**u, "teams": sorted(u["teams"]),
                         "outside": sorted(u["outside"]),
                         "projects": sorted(u["projects"])}
                        for u in users.values()),
                       key=lambda u: (-len(u["projects"]), u["username"]))

    return {"source": inv.get("source"),
            "projects": projects_out, "teams": teams_out, "users": users_out,
            "anomalies": anomalies,
            "summary": {"approvers": len(users_out),
                        "projects_with": sum(1 for p in projects_out if p["approvers"]),
                        "projects_without": sum(1 for p in projects_out
                                                if p["prd_team"] and not p["approvers"]),
                        "teams": len(teams_out),
                        "anomalies": len(anomalies)}}
