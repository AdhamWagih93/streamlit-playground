"""ADO -> Gitea migration: planner (dry-run) + executor.

Builds a consolidated plan from the SAME access analysis the Access page shows
(projects, repos + ACLs, teams + members, [TEAM] LDAP groups, PR reviewers) and
diffs it against the current state of each configured Gitea instance, so every
action is tagged create / exists. Execute performs the create/migrate/grant
calls via the Gitea API and is gated behind an explicit, approver-only confirm."""

import re
import time

from ..config import settings
from . import access, gitea
from .access import _norm_ident, _team_from_desc
from ..auth import ldap_group_members

_CACHE: dict = {"at": 0.0, "payload": None}
_TTL = 900


def _mask(url: str) -> str:
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", url or "")


# ------------------------------------------------------------- targets (config)
def targets() -> list[dict]:
    from ..db import GiteaTarget, SessionLocal
    db = SessionLocal()
    try:
        rows = db.query(GiteaTarget).order_by(GiteaTarget.collection).all()
    finally:
        db.close()
    out = [{"id": r.id, "collection": r.collection, "url": r.url, "token": r.token,
            "org_strategy": r.org_strategy, "added_by": r.added_by} for r in rows]
    if not out and settings.demo_mode:  # seed so the flow is viewable offline
        return [{"id": -1, "collection": "DefaultCollection",
                 "url": "https://gitea.corp.local", "token": "demo", "org_strategy": "project",
                 "added_by": "demo"},
                {"id": -2, "collection": "Research", "url": "https://gitea-research.corp.local",
                 "token": "demo", "org_strategy": "project", "added_by": "demo"}]
    return out


def target_public(t: dict) -> dict:
    return {"id": t["id"], "collection": t["collection"], "url": _mask(t["url"]),
            "org_strategy": t["org_strategy"], "added_by": t["added_by"],
            "has_token": bool(t["token"])}


def add_target(db, collection: str, url: str, token: str,
               org_strategy: str, username: str) -> dict:
    from ..db import GiteaTarget
    collection = (collection or "").strip()
    url = (url or "").strip()
    if not collection:
        raise ValueError("collection is required")
    if not re.match(r"^https?://\S+$", url):
        raise ValueError("Gitea URL must be http(s)")
    if org_strategy not in ("project", "collection_project"):
        org_strategy = "project"
    row = db.query(GiteaTarget).filter(GiteaTarget.collection == collection).first()
    if row is None:
        row = GiteaTarget(collection=collection)
        db.add(row)
    row.url, row.token, row.org_strategy, row.added_by = url, token, org_strategy, username
    db.commit()
    return target_public({"id": row.id, "collection": row.collection, "url": row.url,
                          "token": row.token, "org_strategy": row.org_strategy,
                          "added_by": row.added_by})


def remove_target(db, target_id: int) -> None:
    from ..db import GiteaTarget
    row = db.get(GiteaTarget, target_id)
    if row is not None:
        db.delete(row)
        db.commit()


# ------------------------------------------------------------- plan helpers
def _team_permission(team_name: str, repos: list[dict]) -> str:
    """A team's Gitea permission = its highest ADO privilege tier across the
    repos that grant it (admin > write > read)."""
    order = {"admin": 3, "write": 2, "read": 1, "other": 0}
    key = _norm_ident(team_name)
    best = 0
    for r in repos:
        for a in r.get("acls", []):
            idn = _norm_ident(a["identity"])
            if idn == key or key in idn:
                best = max(best, order.get(a.get("tier", "read"), 1))
    tier = next((t for t, v in order.items() if v == best), "write")
    return gitea.gitea_permission(tier if best else "write")


def _member(identity_or_user: str, username: str | None = None) -> dict:
    """A team member entry: the source identity, the mapped Gitea username, and
    whether that mapping is a best-effort guess (needs verification)."""
    if username:  # from LDAP — a real username, high confidence
        return {"identity": identity_or_user, "gitea_user": username.lower(),
                "verify": False}
    return {"identity": identity_or_user,
            "gitea_user": gitea.gitea_user(identity_or_user),
            "verify": gitea.is_display_name(identity_or_user)}


def _plan_project(p: dict, detail: dict, strategy: str, gstate: dict) -> dict:
    """Map ONE ADO project to a Gitea org plan, diffed against current state."""
    proj = p["name"]
    org = gitea.org_name(proj, p["coll"], strategy)
    cur_org = gstate.get("orgs", {}).get(org, {})
    cur_repos = {r.lower() for r in cur_org.get("repos", [])}
    cur_teams = {t.lower() for t in cur_org.get("teams", {})}

    an = detail.get("analysis", {})
    repos_detail = detail.get("repos", [])
    ado_teams = detail.get("teams", [])

    # repos -> migrate (source pulled from ADO)
    repos = []
    for r in repos_detail:
        gname = gitea.repo_name(r["name"])
        repos.append({"name": r["name"], "gitea_repo": gname,
                      "source_url": r.get("url", ""),
                      "action": "exists" if gname.lower() in cur_repos else "migrate"})

    # collaborators = repo-level identities that are NOT teams/groups (people)
    team_keys = {_norm_ident(t.get("name", "")) for t in ado_teams}
    pr_keys = {_norm_ident(g["name"]) for g in an.get("pr_groups", [])}
    tv = an.get("team_validation") or {}
    ldap_team = tv.get("team") if not tv.get("unassigned") else None
    ldap_key = _norm_ident(ldap_team) if ldap_team else ""
    collaborators = []
    for r in repos_detail:
        for a in r.get("acls", []):
            idn = _norm_ident(a["identity"])
            if idn in team_keys or idn in pr_keys or (ldap_key and (idn == ldap_key or ldap_key in idn)):
                continue  # a team/group — handled as a Gitea team
            m = _member(a["identity"])
            collaborators.append({"repo": gitea.repo_name(r["name"]),
                                  "identity": a["identity"], "gitea_user": m["gitea_user"],
                                  "permission": gitea.gitea_permission(a.get("tier", "read")),
                                  "verify": m["verify"], "action": "grant"})

    # teams: ADO teams (members) + the [TEAM] LDAP group + PR reviewer groups
    teams = []
    for t in ado_teams:
        gname = gitea.team_name(t["name"])
        teams.append({"name": t["name"], "gitea_team": gname, "source": "ado-team",
                      "permission": _team_permission(t["name"], repos_detail),
                      "members": [_member(m) for m in (t.get("members") or [])],
                      "action": "exists" if gname.lower() in cur_teams else "create"})
    if ldap_team:  # the project's [TEAM] LDAP group -> a Gitea team of real usernames
        info = ldap_group_members(ldap_team)
        gname = gitea.team_name(ldap_team)
        teams.append({"name": ldap_team, "gitea_team": gname, "source": "ldap-team",
                      "permission": "write",
                      "members": [_member(m.get("display_name") or m.get("username"),
                                          m.get("username")) for m in info.get("members", [])],
                      "action": "exists" if gname.lower() in cur_teams else "create"})

    # PR reviewers -> a Gitea team + branch protections
    protections = []
    default_repos = [r["gitea_repo"] for r in repos]
    for g in an.get("pr_groups", []):
        gname = gitea.team_name(g["name"])
        teams.append({"name": g["name"], "gitea_team": gname, "source": "pr-reviewers",
                      "permission": "read",
                      "members": [], "member_count": g.get("members"),
                      "action": "exists" if gname.lower() in cur_teams else "create"})
        scope_repos = default_repos if g.get("scope") == "project" else default_repos[:1]
        for rp in scope_repos:
            protections.append({"repo": rp, "branch": "main",
                                "required_approvals": 1, "team": gname,
                                "scope": g.get("scope"), "action": "create"})

    org_action = "exists" if org in gstate.get("orgs", {}) else "create"
    return {"project": proj, "org": org, "org_action": org_action,
            "repos": repos, "teams": teams, "collaborators": collaborators,
            "protections": protections}


# ------------------------------------------------------------- plan
def plan(force: bool = False) -> dict:
    if not force and _CACHE["payload"] and time.time() - _CACHE["at"] < _TTL:
        return {**_CACHE["payload"], "cached": True}

    ado = access.ado_projects()
    all_colls = ado.get("collections", [])
    by_coll: dict[str, list] = {}
    for p in ado.get("projects", []):
        by_coll.setdefault(p["coll"], []).append(p)

    tgts = {t["collection"]: t for t in targets()}
    target_blocks, unconfigured = [], []
    summary = {"orgs_create": 0, "orgs_exists": 0, "repos_migrate": 0, "repos_exists": 0,
               "teams_create": 0, "collaborators": 0, "protections": 0,
               "verify_users": 0, "collections_configured": 0}

    for coll in all_colls:
        t = tgts.get(coll)
        if not t:
            unconfigured.append({"collection": coll,
                                 "projects": len(by_coll.get(coll, []))})
            continue
        summary["collections_configured"] += 1
        gstate = gitea.instance_state(t["url"], t["token"], coll)
        orgs = []
        for p in by_coll.get(coll, []):
            try:
                detail = access.ado_project_access(coll, p["id"])
            except Exception:  # noqa: BLE001 — one project never blocks the plan
                detail = {"teams": [], "repos": [], "analysis": {}}
            block = _plan_project(p, detail, t["org_strategy"], gstate)
            orgs.append(block)
            summary["orgs_create"] += block["org_action"] == "create"
            summary["orgs_exists"] += block["org_action"] == "exists"
            for r in block["repos"]:
                summary["repos_migrate"] += r["action"] == "migrate"
                summary["repos_exists"] += r["action"] == "exists"
            summary["teams_create"] += sum(1 for tm in block["teams"] if tm["action"] == "create")
            summary["collaborators"] += len(block["collaborators"])
            summary["protections"] += len(block["protections"])
            summary["verify_users"] += sum(1 for tm in block["teams"]
                                           for m in tm["members"] if m.get("verify"))
            summary["verify_users"] += sum(1 for c in block["collaborators"] if c.get("verify"))
        cur = gstate.get("orgs", {})
        target_blocks.append({
            "collection": coll, "gitea_url": _mask(t["url"]),
            "org_strategy": t["org_strategy"],
            "state": {"reachable": gstate.get("reachable"), "version": gstate.get("version"),
                      "error": gstate.get("error"), "org_count": len(cur),
                      "repo_count": sum(len(o.get("repos", [])) for o in cur.values()),
                      "team_count": sum(len(o.get("teams", {})) for o in cur.values())},
            "orgs": orgs,
            "projects": len(orgs)})

    payload = {"targets": target_blocks, "unconfigured": unconfigured,
               "summary": summary, "source": ado.get("source", "unknown"),
               "collections": all_colls}
    _CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}


def invalidate() -> None:
    _CACHE.update(at=0.0, payload=None)


# ------------------------------------------------------------- execute
def execute(collection: str | None = None, dry_run: bool = True) -> dict:
    """Perform the migration (or simulate it when dry_run). Returns the FULL
    reconciliation — creates/migrations AND items already present or needing
    manual attention — grouped by target/org, so it's always clear exactly what
    happened (even when there is nothing to do). Live execution is guarded by
    the route (approver-only + explicit)."""
    pln = plan(force=True)
    tgts = {t["collection"]: t for t in targets()}
    steps: list[dict] = []
    counts = {"ok": 0, "skip": 0, "error": 0}

    def log(action, ref, status, note="", org="", target=""):
        steps.append({"action": action, "ref": ref, "status": status,
                      "note": note, "org": org, "target": target})
        counts[status] = counts.get(status, 0) + 1

    planned = [b for b in pln["targets"] if not collection or b["collection"] == collection]
    if not planned:
        why = ("no Gitea targets are configured — add one per collection first"
               if not pln["targets"] else
               f"no Gitea target for collection '{collection}'")
        return {"dry_run": dry_run, "collection": collection, "demo": settings.demo_mode,
                "steps": [], "ok": 0, "skip": 0, "error": 0, "total": 0,
                "targets_run": 0, "note": why}

    for block in planned:
        coll = block["collection"]
        t = tgts.get(coll)
        if not t:
            continue
        live = not dry_run and not settings.demo_mode
        g = None
        if live:
            if not block.get("state", {}).get("reachable"):
                log("target-unreachable", f"{coll} → {_mask(t['url'])}", "error",
                    block.get("state", {}).get("error") or "Gitea not reachable — check URL/token",
                    target=coll)
                continue
            g = gitea.Gitea(t["url"], t["token"])
        for org in block["orgs"]:
            oname = org["org"]
            if org["org_action"] == "create":
                _do(log, dry_run, "create-org", oname,
                    lambda g=g, oname=oname: g.create_org(oname, f"Migrated from ADO {coll}"),
                    org=oname, target=coll)
            else:
                log("org-exists", oname, "skip", "org already in Gitea", org=oname, target=coll)
            for r in org["repos"]:
                ref = f"{oname}/{r['gitea_repo']}"
                if r["action"] == "migrate":
                    _do(log, dry_run, "migrate-repo", ref,
                        lambda g=g, oname=oname, r=r: g.migrate_repo(
                            oname, r["gitea_repo"], r["source_url"],
                            settings.ado_user, settings.ado_git_password),
                        org=oname, target=coll)
                else:
                    log("repo-exists", ref, "skip", "repo already in Gitea", org=oname, target=coll)
            team_ids = {}
            for tm in org["teams"]:
                ref = f"{oname}/{tm['gitea_team']} ({tm['permission']})"
                if tm["action"] == "create":
                    def mk(g=g, oname=oname, tm=tm):
                        tid = g.create_team(oname, tm["gitea_team"], tm["permission"])
                        team_ids[tm["gitea_team"]] = tid
                        for m in tm["members"]:
                            if not m.get("verify"):
                                g.add_team_member(tid, m["gitea_user"])
                    n_add = sum(1 for m in tm["members"] if not m.get("verify"))
                    n_verify = len(tm["members"]) - n_add
                    note = f"+{n_add} member(s)" + (f", {n_verify} need manual verify" if n_verify else "")
                    _do(log, dry_run, "create-team", ref, mk, note=note, org=oname, target=coll)
                else:
                    log("team-exists", ref, "skip", "team already in Gitea", org=oname, target=coll)
            for c in org["collaborators"]:
                ref = f"{c['gitea_user']}@{oname}/{c['repo']} ({c['permission']})"
                if c.get("verify"):
                    log("collaborator-verify", ref, "skip",
                        "display-name→username unverified — grant manually", org=oname, target=coll)
                else:
                    _do(log, dry_run, "add-collaborator", ref,
                        lambda g=g, oname=oname, c=c: g.add_collaborator(
                            oname, c["repo"], c["gitea_user"], c["permission"]),
                        org=oname, target=coll)
            for pr in org["protections"]:
                _do(log, dry_run, "branch-protection",
                    f"{oname}/{pr['repo']}@{pr['branch']} ≥{pr['required_approvals']} ({pr['team']})",
                    lambda g=g, oname=oname, pr=pr: g.create_branch_protection(
                        oname, pr["repo"], pr["branch"], pr["required_approvals"], pr["team"]),
                    org=oname, target=coll)
    if not dry_run:
        invalidate()
    note = ""
    if counts["ok"] == 0 and counts["error"] == 0 and counts["skip"]:
        note = "nothing to migrate — every org, repo and team already exists in Gitea"
    return {"dry_run": dry_run, "collection": collection, "demo": settings.demo_mode,
            "steps": steps, "ok": counts["ok"], "skip": counts["skip"],
            "error": counts["error"], "total": len(steps),
            "targets_run": len(planned), "note": note}


def _do(log, dry_run: bool, action: str, ref: str, fn,
        note: str = "", org: str = "", target: str = "") -> None:
    if dry_run:
        log(action, ref, "ok", ("would run" + (f" · {note}" if note else "")), org=org, target=target)
        return
    if settings.demo_mode:
        log(action, ref, "ok", "demo — not executed", org=org, target=target)
        return
    try:
        fn()
        log(action, ref, "ok", ("done" + (f" · {note}" if note else "")), org=org, target=target)
    except Exception as exc:  # noqa: BLE001 — record and continue the migration
        log(action, ref, "error", str(exc)[:200], org=org, target=target)
