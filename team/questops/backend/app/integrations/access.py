"""Access Management: who can do what, across the three source systems.

  ADO     — per-project teams/members + per-repository ACLs (Git security
            namespace, allow/deny bitmasks decoded to permission names)
  Jira    — permission schemes ("templates"): every permission grant and
            which projects each scheme is assigned to
  Jenkins — Matrix-based RBAC: <permission> entries from job/folder
            config.xml (project-based matrix authorization)

Built to NOT overload the sources: everything is cached server-side
(15 min TTL, explicit refresh), ADO project detail loads only on expand,
Jenkins config.xml fetches go through one shared cache, and ADO identity
descriptors are resolved in batches."""

import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

from ..auth import ldap_group_members, team_source_status
from ..config import settings

TTL = 900
HTTP_TIMEOUT = 20
HTTP_CONNECT = 5
POOL = 12          # concurrent fetches against a source (bounded, not a flood)
_CACHE: dict = {}

# ADO Git repositories security namespace + its permission bits
ADO_GIT_NAMESPACE = "2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87"
ADO_GIT_BITS = [
    (1, "Administer"), (2, "Read"), (4, "Contribute"), (8, "Force push"),
    (16, "Create branch"), (32, "Create tag"), (64, "Manage notes"),
    (128, "Bypass policies (PR)"), (256, "Create repository"),
    (512, "Delete repository"), (1024, "Rename repository"),
    (2048, "Edit policies"), (4096, "Remove others' locks"),
    (8192, "Manage permissions"), (16384, "Contribute to PRs"),
    (32768, "Bypass policies (push)"),
]


def _cached(key: str, force: bool, builder):
    hit = _CACHE.get(key)
    if hit and not force and time.time() - hit["at"] < TTL:
        return {**hit["payload"], "cached": True, "cached_at": hit["at"]}
    payload = builder()
    _CACHE[key] = {"at": time.time(), "payload": payload}
    return {**payload, "cached": False, "cached_at": time.time()}


def _decode_bits(mask: int) -> list[str]:
    return [name for bit, name in ADO_GIT_BITS if mask & bit]


_ADMIN_PERMS = {"Administer", "Manage permissions", "Edit policies", "Delete repository"}
_WRITE_PERMS = {"Contribute", "Force push", "Create branch", "Create tag",
                "Contribute to PRs", "Bypass policies (PR)", "Bypass policies (push)"}


def _privilege_tier(allow: list[str]) -> str:
    a = set(allow)
    if a & _ADMIN_PERMS:
        return "admin"
    if a & _WRITE_PERMS:
        return "write"
    if "Read" in a:
        return "read"
    return "other"


def _pct(n: int, total: int) -> int:
    return round(n / total * 100) if total else 0


def _short_http(exc: Exception) -> str:
    """Compact 'HTTP 404 at /path' from a requests exception, for the UI."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        url = str(getattr(resp, "url", "")).split("?")[0]
        tail = "/" + url.split("/_apis/", 1)[1] if "/_apis/" in url else url[-60:]
        return f"HTTP {resp.status_code} at …{tail}"
    return str(exc)[:100]


# ================================================================= ADO
from . import ado as _ado


def _excluded_accounts() -> set[str]:
    """Service account + configured repo-creator/admin exclusions — all
    ignored in repo ACL analysis so they don't skew repo-specific detection."""
    out = set(settings.ado_access_exclude_list)
    if settings.ado_user:
        out.add(settings.ado_user.strip().lower())
    return out


def _is_service_account(identity: str, descriptor: str = "") -> bool:
    """True if this identity is the service account or a configured exclusion
    (repo creators/admins). Matched against bare/domain/UPN forms."""
    excl = _excluded_accounts()
    if not excl:
        return False
    ident = identity.strip().lower()
    tokens = set(re.split(r"[\\/@ ]", f"{ident} {descriptor}".lower()))
    return any(e == ident or e in tokens for e in excl)


PROJECT_SCORE_REPO_CAP = 2500  # bound the upfront ACL sweep (whole instance)
TEAM_MEMBER_CALL_CAP = 3000    # bound the upfront team-member sweep (member counts)
# per-project repo cap applied IDENTICALLY in the list-sweep and the detail
# expand, so the badge score and the expanded score are always the same set
PROJECT_REPO_CAP = 200


def _grade(score) -> str:
    if score is None:
        return "?"
    return ("A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60
            else "D" if score >= 40 else "F")


def _score_project(an: dict):
    """0-100 access-hygiene score: uniform access, low repo-specific sprawl,
    low admin concentration, and (when a [TEAM] is set) the team group being
    granted with no out-of-team grantees, all score high. None = no repos."""
    if not an.get("total_repos"):
        return None
    s = 100.0
    s -= an["pct_repo_specific"] * 0.4                       # up to -40
    s -= min(max(an["distinct_acl_sets"] - 1, 0), 10) * 3    # up to -30
    s -= an["pct_admin"] * 0.3                               # up to -30
    tv = an.get("team_validation")
    if tv:
        if not tv.get("ldap_resolved"):
            s -= 15    # [TEAM] set but its LDAP group couldn't be resolved/validated
        else:
            if not tv["group_granted"]:
                s -= 15                                      # team group not granted
            s -= min(tv["non_team_count"], 5) * 6           # up to -30 out-of-team grants
    return max(0, min(100, round(s)))


def _collection_rollup(projects: list[dict], colls: list[str]) -> list[dict]:
    """Per-collection aggregates + access-hygiene rollup for the (collapsed)
    collection headers: how many projects are uniform vs repo-specific, and
    an overall score (average of scored projects)."""
    out = []
    for c in colls:
        ps = [p for p in projects if p["coll"] == c]
        scored = [p["score"] for p in ps if p.get("score") is not None]
        with_repos = [p for p in ps if p.get("repos")]
        uniform = sum(1 for p in with_repos if p.get("uniform"))
        repo_specific = sum(1 for p in with_repos if p.get("uniform") is False)
        # distinct members across the whole collection (people in >1 project
        # counted once); falls back to summing per-project counts pre-sweep
        member_sets = [p.get("_memberset") for p in ps if p.get("_memberset") is not None]
        distinct_members = (len(set().union(*member_sets)) if member_sets
                            else sum(p.get("members", 0) for p in ps))
        # team-governance breakdown
        team_defined = [p for p in ps if p.get("team") and not p.get("team_unassigned")
                        and p.get("team_ldap_resolved")]
        whole_team = sum(1 for p in team_defined if p.get("team_group_granted"))
        unassigned = [p for p in ps if p.get("team_unassigned")]
        unassigned_healthy = sum(1 for p in unassigned if p.get("team_ok"))
        extra_member_projects = sum(1 for p in ps if (p.get("team_non_member_count") or 0) > 0)
        dup_projects = sum(1 for p in ps if (p.get("team_duplicate_count") or 0) > 0)
        ldap_failed = sum(1 for p in ps if p.get("team") and not p.get("team_unassigned")
                          and p.get("team_ldap_resolved") is False)
        # PR-reviewer governance: of the projects that HAVE repos, how many
        # define a PR-reviewer group, split by project-level vs repo-level scope
        pr_scored = [p for p in with_repos if p.get("score") is not None]
        pr_defined = [p for p in pr_scored if p.get("pr_present")]
        pr_project_level = sum(1 for p in pr_defined if p.get("pr_scope") == "project")
        pr_repo_level = sum(1 for p in pr_defined if p.get("pr_scope") == "repo")
        out.append({
            "name": c, "projects": len(ps),
            "teams": sum(p.get("teams", 0) for p in ps),
            "repos": sum(p.get("repos", 0) for p in ps),
            "members": distinct_members,
            "uniform_projects": uniform, "repo_specific_projects": repo_specific,
            "team_defined_projects": len(team_defined),
            "whole_team_projects": whole_team,
            "per_member_projects": len(team_defined) - whole_team,
            "unassigned_projects": len(unassigned),
            "unassigned_healthy": unassigned_healthy,
            "unassigned_unhealthy": len(unassigned) - unassigned_healthy,
            "extra_member_projects": extra_member_projects,
            "duplicate_grant_projects": dup_projects,
            "ldap_failed_projects": ldap_failed,
            "pr_scored_projects": len(pr_scored),
            "pr_defined_projects": len(pr_defined),
            "pr_project_level": pr_project_level,
            "pr_repo_level": pr_repo_level,
            "pr_missing_projects": len(pr_scored) - len(pr_defined),
            "score": round(sum(scored) / len(scored)) if scored else None,
            "grade": _grade(round(sum(scored) / len(scored)) if scored else None)})
    return out


def ado_projects(force: bool = False) -> dict:
    def build():
        if settings.demo_mode:
            return _demo_ado_projects()
        if not settings.ado_url:
            return {"source": "not configured", "projects": [], "collections": [],
                    "collection_stats": []}
        colls = _ado.collections(force)

        def coll_projects(coll):
            try:
                data = _ado.coll_get(coll, "/_apis/projects", {"$top": 500})
                return [{"id": p["id"], "coll": coll, "name": p["name"],
                         "description": (p.get("description") or "")[:160],
                         "url": _ado.project_url(coll, p["name"])}
                        for p in _values(data) if p]
            except requests.RequestException:
                return []

        with ThreadPoolExecutor(max_workers=POOL) as pool:
            projects = [p for group in pool.map(coll_projects, colls) for p in group]

        # repo lists (id+name) + team counts per project, in parallel
        def proj_repos(p):
            try:
                rl = [{"id": r["id"], "name": r["name"]}
                      for r in (_ado.coll_get(
                          p["coll"], f"/{p['id']}/_apis/git/repositories").get("value") or [])
                      if r.get("id") and r.get("name")]
                return sorted(rl, key=lambda r: r["name"].lower())  # deterministic cap
            except Exception:  # noqa: BLE001 — one bad project must not fail the sweep
                return []

        # teams (id+name) per project — members fetched below only for
        # projects that have a [TEAM] to validate (spares the instance)
        def proj_teams(p):
            try:
                return [{"id": t.get("id", ""), "name": t.get("name", "")}
                        for t in _values(_ado.coll_get(
                            p["coll"], f"/_apis/projects/{p['id']}/teams", {"$top": 500}))
                        if t]
            except Exception:  # noqa: BLE001
                return []

        with ThreadPoolExecutor(max_workers=POOL) as pool:
            repo_lists = list(pool.map(proj_repos, projects))
            team_lists = list(pool.map(proj_teams, projects))
        # repo NAME occurrences across the WHOLE instance — to flag the same
        # repo name living in more than one project/collection
        repo_occ: dict[str, list] = {}
        for p, rl, tl in zip(projects, repo_lists, team_lists):
            # SAME per-project cap as the detail expand → identical scores
            p["repos"], p["teams"] = len(rl), len(tl)
            p["_repolist"] = rl[:PROJECT_REPO_CAP]
            p["_teamlist"] = tl
            for r in rl:
                repo_occ.setdefault(r["name"].lower(), []).append(
                    {"name": r["name"], "project": p["name"], "coll": p["coll"]})
        duplicate_repos = [
            {"name": occ[0]["name"], "count": len(occ),
             "locations": sorted(occ, key=lambda x: (x["coll"].lower(), x["project"].lower()))}
            for occ in repo_occ.values() if len(occ) > 1]
        duplicate_repos.sort(key=lambda d: (-d["count"], d["name"].lower()))

        # ONE flat, bounded ACL sweep across every (capped) repo of every
        # project so the collapsed view can show a hygiene SCORE without
        # expanding each. Whole-instance cap protects a huge instance.
        pairs = [(i, r) for i, p in enumerate(projects) for r in p["_repolist"]]
        capped = pairs[:PROJECT_SCORE_REPO_CAP]

        def fetch_acl(pair):
            i, r = pair
            p = projects[i]
            try:
                acl = _ado.coll_get(
                    p["coll"], f"/_apis/accesscontrollists/{ADO_GIT_NAMESPACE}",
                    {"token": f"repoV2/{p['id']}/{r['id']}"})
                aces = {}
                for e in (acl.get("value") or []):
                    aces.update(e.get("acesDictionary") or {})
                return i, r["name"], aces
            except Exception:  # noqa: BLE001
                return i, r["name"], {}

        by_proj: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=POOL) as pool:
            for i, rname, aces in pool.map(fetch_acl, capped):
                by_proj.setdefault(i, {})[rname] = aces

        # fully-swept projects only (all their repos got ACLs); others -> '?'
        counts_in_sweep: dict[int, int] = {}
        for i, _ in capped:
            counts_in_sweep[i] = counts_in_sweep.get(i, 0) + 1
        fully = {i for i, p in enumerate(projects)
                 if counts_in_sweep.get(i, 0) == len(p["_repolist"])}

        # only projects with a [TEAM] need LDAP + ADO-team-member fetches
        team_projects = {i for i in fully
                         if _team_from_desc(projects[i].get("description", ""))}

        # LDAP members for every referenced [TEAM] group (skip [UnAssigned],
        # which is not a real group), cached
        teams_needed = {t for t in (_team_from_desc(projects[i]["description"])
                                    for i in team_projects)
                        if _norm_ident(t) != "unassigned"}
        ldap_by_team: dict[str, list] = {}
        if teams_needed:
            with ThreadPoolExecutor(max_workers=POOL) as pool:
                for t, mem in pool.map(lambda t: (t, ldap_group_members(t)),
                                       sorted(teams_needed)):
                    ldap_by_team[t] = mem

        # ADO team MEMBERS for ALL scored projects — the real grantees. Used
        # for the [TEAM] validation AND for the per-project / per-collection
        # member counts. One flat bounded sweep; badge validation matches the
        # detail expand for [TEAM] projects.
        team_member_pairs = [(i, t) for i in sorted(fully)
                             for t in projects[i]["_teamlist"]]
        tm_capped = team_member_pairs[:TEAM_MEMBER_CALL_CAP]

        def fetch_members(pair):
            i, t = pair
            p = projects[i]
            try:
                data = _ado.coll_get(
                    p["coll"], f"/_apis/projects/{p['id']}/teams/{t['id']}/members",
                    {"$top": 500})
                mem = sorted({(m.get("identity") or m).get("displayName", "")
                              for m in _values(data)
                              if m and (m.get("identity") or m).get("displayName")
                              and not _is_service_account((m.get("identity") or m).get("displayName", ""))})
                return i, {"name": t["name"], "members": mem}
            except Exception:  # noqa: BLE001
                return i, {"name": t["name"], "members": []}

        ado_teams_by_proj: dict[int, list] = {}
        with ThreadPoolExecutor(max_workers=POOL) as pool:
            for i, team in pool.map(fetch_members, tm_capped):
                ado_teams_by_proj.setdefault(i, []).append(team)

        # distinct member set per project (union across its teams) — drives
        # the per-project count and the per-collection roll-up
        for i in fully:
            mset = {m for t in ado_teams_by_proj.get(i, []) for m in t["members"]}
            projects[i]["members"] = len(mset)
            projects[i]["_memberset"] = mset

        # PROJECT-level Git ACL (token repoV2/{projectId}, no repo id) per scored
        # project — lets us tell a project-wide PR-reviewer grant from a
        # repo-specific one. One bounded parallel pass.
        def fetch_proj_acl(i):
            p = projects[i]
            try:
                acl = _ado.coll_get(
                    p["coll"], f"/_apis/accesscontrollists/{ADO_GIT_NAMESPACE}",
                    {"token": f"repoV2/{p['id']}"})
                aces = {}
                for e in _values(acl):
                    aces.update(e.get("acesDictionary") or {})
                return i, aces
            except Exception:  # noqa: BLE001
                return i, {}

        proj_acl_by_proj: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=POOL) as pool:
            for i, aces in pool.map(fetch_proj_acl, sorted(fully)):
                proj_acl_by_proj[i] = aces

        # resolve identities PER PROJECT — identical to the detail path, so a
        # throttled global batch can't desync the badge from the expanded view
        def score_one(i):
            p = projects[i]
            try:
                raw = by_proj.get(i, {})
                proj_aces = proj_acl_by_proj.get(i, {})
                # resolve repo-level AND project-level identities together
                descs = sorted({d for aces in raw.values() for d in aces} | set(proj_aces))
                names_i = _resolve_identities(descs)
                repos = _build_repos(p["coll"], p["id"], p["_repolist"], raw, names_i)
                desc = p.get("description", "")
                ldap_info = ldap_by_team.get(_team_from_desc(desc))
                ado_teams = ado_teams_by_proj.get(i, [])
                pr_ctx = _build_pr_ctx(raw, proj_aces, names_i)
                return i, _project_access_analysis(repos, ado_teams, desc,
                                                   ldap_info, pr_ctx)
            except Exception:  # noqa: BLE001 — degrade this project to unscored
                return i, None

        with ThreadPoolExecutor(max_workers=POOL) as pool:
            for i, an in pool.map(score_one, sorted(fully)):
                if an is None:
                    fully.discard(i)
                    continue
                tv = an.get("team_validation")
                projects[i].update(score=an["score"], grade=an["grade"],
                                   uniform=an["uniform"],
                                   pct_repo_specific=an["pct_repo_specific"],
                                   team=(tv or {}).get("team"),
                                   team_unassigned=(tv or {}).get("unassigned", False),
                                   team_ldap_resolved=(tv or {}).get("ldap_resolved"),
                                   team_ok=(tv is not None and tv["ldap_resolved"]
                                            and tv["group_granted"]
                                            and tv["non_team_count"] == 0),
                                   team_group_granted=(tv or {}).get("group_granted"),
                                   team_non_member_count=(tv or {}).get("non_team_count"),
                                   team_duplicate_count=(tv or {}).get("duplicate_count"),
                                   pr_groups=an["pr_groups"], pr_present=an["pr_present"],
                                   pr_scope=an["pr_scope"],
                                   pr_member_count=an["pr_member_count"])
        for i, p in enumerate(projects):
            if i not in fully:
                p.update(score=None, grade="?", uniform=None,
                         pct_repo_specific=None, not_scored=True)
            p.pop("_repolist", None)
            p.pop("_teamlist", None)

        projects.sort(key=lambda p: (p["coll"].lower(), p["name"].lower()))
        stats = _collection_rollup(projects, colls)  # uses _memberset (distinct)
        for p in projects:
            p.pop("_memberset", None)
        return {"source": "live", "projects": projects, "collections": colls,
                "collection_stats": stats,
                "ldap_failed_teams": _group_ldap_failures(projects),
                "duplicate_repos": duplicate_repos[:200],
                "duplicate_repo_count": len(duplicate_repos),
                "scored_repos": len(capped), "total_repos": len(pairs)}
    return _cached("ado:projects", force, build)


def _group_ldap_failures(projects: list[dict]) -> list[dict]:
    """Failed LDAP-group validations grouped by UNIQUE team first, then the
    projects using it — so a group missing from LDAP is reported once, with
    all affected projects under it."""
    by_team: dict[str, list] = {}
    for p in projects:
        if p.get("team") and not p.get("team_unassigned") \
                and p.get("team_ldap_resolved") is False:
            by_team.setdefault(p["team"], []).append(
                {"project": p["name"], "coll": p["coll"]})
    return [{"team": t, "count": len(ps),
             "projects": sorted(ps, key=lambda x: (x["coll"], x["project"]))}
            for t, ps in sorted(by_team.items())]


def _demo_ado_projects() -> dict:
    # p1 Platform: repo-specific (Engine/UI differ); p2 Control: uniform;
    # p3 Sandbox: uniform — exercises the scoring + rollup
    projects = [
        {"id": "p1", "coll": "DefaultCollection", "name": "Platform",
         "description": "[platform-devs] Product delivery", "repos": 6, "teams": 3,
         "members": 5, "_memberset": {"Alice Nasr", "Bob Farid", "Carol Adel",
                                      "Dave Samir", "Erin Zaki"},
         "score": 62, "grade": "C", "uniform": False, "pct_repo_specific": 100,
         "team": "platform-devs", "team_ok": False, "team_group_granted": True,
         "team_non_member_count": 1, "team_ldap_resolved": True, "team_duplicate_count": 4,
         "pr_present": True, "pr_scope": "project", "pr_member_count": 3,
         "pr_groups": [{"name": "PR Approvers", "scope": "project", "members": 3}],
         "url": "https://ado.demo/DefaultCollection/Platform"},
        {"id": "p2", "coll": "DefaultCollection", "name": "Control",
         "description": "[control-owners] Team config repos", "repos": 2, "teams": 1,
         "members": 2, "_memberset": {"Alice Nasr", "Bob Farid"},
         "score": 94, "grade": "A", "uniform": True, "pct_repo_specific": 50,
         "team": "control-owners", "team_ok": True, "team_group_granted": True,
         "team_non_member_count": 0, "team_ldap_resolved": True,
         "pr_present": True, "pr_scope": "repo", "pr_member_count": 2,
         "pr_groups": [{"name": "PR", "scope": "repo", "members": 2}],
         "url": "https://ado.demo/DefaultCollection/Control"},
        {"id": "p3", "coll": "Research", "name": "Sandbox",
         "description": "[sandbox-team] Experiments", "repos": 1, "teams": 1,
         "members": 1, "_memberset": {"Carol Adel"},
         "score": 63, "grade": "C", "uniform": True, "pct_repo_specific": 100,
         "team": "sandbox-team", "team_ok": False, "team_group_granted": False,
         "team_non_member_count": 0, "team_ldap_resolved": False,
         "pr_present": False, "pr_scope": None, "pr_member_count": 0, "pr_groups": [],
         "url": "https://ado.demo/Research/Sandbox"},
        {"id": "p4", "coll": "Research", "name": "Attic",
         "description": "[UnAssigned] retired area", "repos": 1, "teams": 0,
         "members": 0, "_memberset": set(),
         "score": 100, "grade": "A", "uniform": True, "pct_repo_specific": 0,
         "team": "UnAssigned", "team_unassigned": True, "team_ok": True,
         "team_group_granted": True, "team_non_member_count": 0,
         "team_ldap_resolved": True,
         "url": "https://ado.demo/Research/Attic"},
        # same NAME as the 'Platform' project in DefaultCollection — exercises
        # the cross-collection duplicate-name highlight
        {"id": "p5", "coll": "Research", "name": "Platform",
         "description": "[research-team] Research platform sandbox", "repos": 1, "teams": 1,
         "members": 1, "_memberset": {"Carol Adel"},
         "score": 80, "grade": "B", "uniform": True, "pct_repo_specific": 0,
         "team": "research-team", "team_ok": True, "team_group_granted": True,
         "team_non_member_count": 0, "team_ldap_resolved": True,
         "pr_present": False, "pr_scope": None, "pr_member_count": 0, "pr_groups": [],
         "url": "https://ado.demo/Research/Platform"},
    ]
    colls = ["DefaultCollection", "Research"]
    stats = _collection_rollup(projects, colls)
    failed = _group_ldap_failures(projects)
    for p in projects:
        p.pop("_memberset", None)
    # 'prototypes' lives in both Research/Sandbox and Research/Platform; 'sandbox'
    # in Research/Platform too — exercises the cross-instance duplicate-repo view
    duplicate_repos = [
        {"name": "prototypes", "count": 2, "locations": [
            {"name": "prototypes", "project": "Platform", "coll": "Research"},
            {"name": "prototypes", "project": "Sandbox", "coll": "Research"}]},
    ]
    return {"source": "demo", "projects": projects, "collections": colls,
            "collection_stats": stats,
            "ldap_failed_teams": failed,
            "duplicate_repos": duplicate_repos, "duplicate_repo_count": len(duplicate_repos),
            "scored_repos": 9, "total_repos": 9}


def _values(data) -> list:
    """The 'value' array from an ADO response, resilient to None / null."""
    return (data or {}).get("value") or []


def _resolve_identities(descriptors: list[str]) -> dict[str, str]:
    """descriptor -> display name, batched to spare the identity service."""
    out: dict[str, str] = {}
    for i in range(0, len(descriptors), 50):
        batch = descriptors[i:i + 50]
        try:
            data = _ado.get("/_apis/identities", {"descriptors": ",".join(batch)})
            for ident in _values(data):
                if ident:
                    out[ident.get("descriptor", "")] = (
                        ident.get("providerDisplayName")
                        or ident.get("customDisplayName") or "")
        except requests.RequestException:
            continue
    return out


def _demo_project_access(project_id: str) -> dict:
    su = settings.ado_user or "svc-questops"
    teams = [{"name": "Platform Team",
              "members": ["Alice Nasr", "Bob Farid", "Carol Adel", "Dave Samir",
                          "Erin Zaki"]},  # Erin is NOT in the platform-devs LDAP group
             {"name": "Platform Admins", "members": ["Alice Nasr"]}]
    # a grant to the service account — MUST be filtered out of the output
    raw_repos = [
        {"name": "Engine", "acls": [
            # the WHOLE [TEAM] LDAP group granted directly — so any individual
            # grant to a member below is redundant/duplicate access
            {"identity": "[Platform]\\platform-devs",
             "allow": ["Read", "Contribute"], "deny": []},
            {"identity": "[Platform]\\Platform Team",
             "allow": ["Read", "Contribute", "Create branch", "Create tag"], "deny": []},
            {"identity": "[Platform]\\Platform Admins",
             "allow": ["Administer", "Read", "Contribute", "Force push",
                       "Edit policies", "Manage permissions"], "deny": []},
            {"identity": su, "allow": ["Read", "Contribute"], "deny": []},
            {"identity": "[Platform]\\Contractors",
             "allow": ["Read"], "deny": ["Contribute", "Force push"]},
        ]},
        {"name": "UI", "acls": [
            {"identity": "[Platform]\\Platform Team",
             "allow": ["Read", "Contribute"], "deny": []},
        ]},
    ]
    coll = "DefaultCollection"
    if project_id == "p2":
        teams = [{"name": "Control Owners", "members": ["Alice Nasr", "Bob Farid"]}]
        raw_repos = [{"name": "team-configs", "acls": [
            {"identity": "[Control]\\Control Owners",
             "allow": ["Administer", "Read", "Contribute"], "deny": []},
            {"identity": "[Control]\\PR",  # repo-level PR reviewers
             "allow": ["Read", "Contribute to PRs"], "deny": []},
            {"identity": "[Control]\\Everyone", "allow": ["Read"], "deny": []},
        ]}]
    elif project_id == "p3":
        coll = "Research"
        teams = [{"name": "Sandbox Team", "members": ["Carol Adel"]}]
        raw_repos = [{"name": "prototypes", "acls": [
            {"identity": "[Research]\\Sandbox Team",
             "allow": ["Read", "Contribute"], "deny": []}]}]
    elif project_id == "p4":  # [UnAssigned] — healthy: nobody has access
        coll = "Research"
        teams = []
        raw_repos = [{"name": "old-archive", "acls": []}]
    elif project_id == "p5":  # 'Platform' in Research — a cross-collection name twin
        coll = "Research"
        teams = [{"name": "Research Team", "members": ["Carol Adel"]}]
        raw_repos = [{"name": "sandbox", "acls": [
            {"identity": "[Research]\\Research Team",
             "allow": ["Read", "Contribute"], "deny": []}]}]
    repos = []
    for r in raw_repos:
        # demo has no real ADO_USER; filter against the injected demo account
        acls = [dict(a, tier=_privilege_tier(a["allow"]))
                for a in r["acls"] if a["identity"].strip().lower() != su.strip().lower()]
        repos.append({"name": r["name"], "acls": acls,
                      "signature": _acl_signature(acls),
                      "url": f"https://ado.demo/{coll}/{project_id}/_git/{r['name']}"})
    demo_desc = {"p1": "[platform-devs] Product delivery",
                 "p2": "[control-owners] Team config repos",
                 "p3": "[sandbox-team] Experiments",
                 "p4": "[UnAssigned] retired area",
                 "p5": "[research-team] Research platform sandbox"}.get(project_id, "")
    ldap_members = ldap_group_members(_team_from_desc(demo_desc))
    # p1: project-level PR Approvers (3); p2: repo-level PR (2, from the ACL above)
    pr_ctx = {"p1": {"project_acl_names": ["[Platform]\\PR Approvers"],
                     "counts_by_name": {"prapprovers": 3}},
              "p2": {"project_acl_names": [], "counts_by_name": {"pr": 2}}
              }.get(project_id, {})
    return {"source": "demo", "teams": teams, "repos": repos,
            "analysis": _project_access_analysis(repos, teams, demo_desc,
                                                 ldap_members, pr_ctx)}


def ado_project_access(collection: str, project_id: str,
                       force: bool = False) -> dict:
    """Teams+members and per-repo ACLs for ONE project — fetched on expand.
    Access granted to QO_ADO_USER is filtered out."""
    def build():
        if settings.demo_mode:
            return _demo_project_access(project_id)
        if not settings.ado_url:
            return {"source": "not configured", "teams": [], "repos": []}
        errors = []  # per-call failures surface inline instead of blanking

        # teams (project-scoped) — non-fatal
        teams = []
        try:
            tdata = _ado.coll_get(collection, f"/_apis/projects/{project_id}/teams",
                                  {"$top": 100})
            for t in _values(tdata):
                members = []
                try:
                    data = _ado.coll_get(
                        collection,
                        f"/_apis/projects/{project_id}/teams/{t['id']}/members",
                        {"$top": 200})
                    members = [(m.get("identity") or m).get("displayName", "")
                               for m in _values(data) if m]
                except requests.RequestException:
                    pass
                teams.append({"name": t.get("name", ""),
                              "members": sorted(m for m in filter(None, members)
                                                if not _is_service_account(m))})
        except requests.RequestException as exc:
            errors.append(f"teams: {_short_http(exc)}")

        # repos — PROJECT-scoped, capped the SAME as the list-sweep so the
        # expanded score matches the badge exactly
        try:
            repo_list = sorted(
                (r for r in _values(_ado.coll_get(
                    collection, f"/{project_id}/_apis/git/repositories")) if r),
                key=lambda r: r.get("name", "").lower())[:PROJECT_REPO_CAP]
        except requests.RequestException as exc:
            errors.append(f"repositories: {_short_http(exc)}")
            repo_list = []
        descriptors: set[str] = set()
        raw_acls: dict[str, dict] = {}

        def fetch_acl(rp):
            try:
                acl = _ado.coll_get(
                    collection, f"/_apis/accesscontrollists/{ADO_GIT_NAMESPACE}",
                    {"token": f"repoV2/{project_id}/{rp['id']}"})
                aces = {}
                for entry in _values(acl):
                    aces.update(entry.get("acesDictionary") or {})
                return rp["name"], aces
            except Exception:  # noqa: BLE001
                return rp["name"], {}

        with ThreadPoolExecutor(max_workers=POOL) as pool:  # parallel ACL reads
            for name, aces in pool.map(fetch_acl, repo_list):
                raw_acls[name] = aces
                descriptors.update(aces.keys())
        # PROJECT-level Git ACL (no repo id) — distinguishes a project-wide
        # PR-reviewer grant from a repo-specific one
        proj_aces: dict = {}
        try:
            pacl = _ado.coll_get(
                collection, f"/_apis/accesscontrollists/{ADO_GIT_NAMESPACE}",
                {"token": f"repoV2/{project_id}"})
            for entry in _values(pacl):
                proj_aces.update(entry.get("acesDictionary") or {})
            descriptors.update(proj_aces.keys())
        except requests.RequestException as exc:
            errors.append(f"project acl: {_short_http(exc)}")
        names = _resolve_identities(sorted(descriptors))
        repos = _build_repos(collection, project_id, repo_list, raw_acls, names)
        # project description carries the [TEAM] LDAP group for validation
        description = ""
        try:
            description = (_ado.coll_get(
                collection, f"/_apis/projects/{project_id}") or {}).get("description") or ""
        except requests.RequestException as exc:
            errors.append(f"project: {_short_http(exc)}")
        ldap_members = ldap_group_members(_team_from_desc(description))
        pr_ctx = _build_pr_ctx(raw_acls, proj_aces, names)
        analysis = _project_access_analysis(repos, teams, description,
                                            ldap_members, pr_ctx)
        return {"source": "live", "teams": teams, "repos": repos,
                "analysis": analysis,
                "repo_cap_note": len(repo_list) >= PROJECT_REPO_CAP, "errors": errors}
    return _cached(f"ado:project:{collection}:{project_id}", force, build)


def _acl_signature(acls: list[dict]) -> str:
    return "|".join(sorted(f"{a['identity']}:{a['tier']}" for a in acls))


def _build_repos(collection: str, project_id: str, repo_list: list[dict],
                 raw_acls: dict, names: dict) -> list[dict]:
    """Repos with filtered/tiered/signed ACLs — the shared shape used by both
    the project detail and the upfront scoring sweep."""
    repos = []
    for rp in repo_list:
        acls = []
        for desc, ace in (raw_acls.get(rp["name"]) or {}).items():
            ident = names.get(desc) or desc[:60]
            if _is_service_account(ident, desc):  # service acct + exclusions
                continue
            allow = _decode_bits(ace.get("allow", 0))
            deny = _decode_bits(ace.get("deny", 0))
            if allow or deny:
                acls.append({"identity": ident, "allow": allow, "deny": deny,
                             "tier": _privilege_tier(allow)})
        acls.sort(key=lambda a: a["identity"].lower())
        repos.append({"name": rp["name"], "acls": acls,
                      "signature": _acl_signature(acls),
                      "url": _ado.repo_url(collection, project_id, rp["name"])})
    repos.sort(key=lambda r: r["name"].lower())
    return repos


def _team_from_desc(description: str) -> str:
    """The LDAP group in a '[TEAM] ...' project description."""
    m = re.match(r"\s*\[([^\]]+)\]", description or "")
    return m.group(1).strip() if m else ""


def _norm_ident(s: str) -> str:
    """Last path segment of an identity, normalized for member matching."""
    s = re.split(r"[\\/]", (s or "").strip())[-1]
    return re.sub(r"[\s_\-.]+", "", s.lower())


# PR-reviewer groups: an ADO team/group whose name marks it as pull-request
# approvers/reviewers — "PR", "PR Approvers", "PR Reviewers", "Pull Request
# Approvers/Reviewers" (case/separator-insensitive; _norm_ident strips those).
_PR_GROUP_RE = re.compile(r"^(?:pr|pullrequest)(?:approvers?|reviewers?)?$")


def _is_pr_group(name: str) -> bool:
    return bool(_PR_GROUP_RE.fullmatch(_norm_ident(name)))


def _group_member_counts(descriptors: list[str]) -> dict[str, int]:
    """descriptor -> EXPANDED member count, for security groups referenced in
    ACLs (used to size PR-reviewer groups that aren't ADO teams). Batched;
    best-effort — a descriptor missing from the result just has no count."""
    out: dict[str, int] = {}
    for i in range(0, len(descriptors), 50):
        batch = descriptors[i:i + 50]
        try:
            data = _ado.get("/_apis/identities",
                            {"descriptors": ",".join(batch),
                             "queryMembership": "Expanded"})
            for ident in _values(data):
                if ident and ident.get("descriptor"):
                    out[ident["descriptor"]] = len(ident.get("members") or [])
        except requests.RequestException:
            continue
    return out


def _build_pr_ctx(repo_aces_by_repo: dict, proj_aces: dict,
                  names: dict) -> dict:
    """Assemble the PR-group context for _project_access_analysis from resolved
    ACLs: the PROJECT-level identity names, plus a name->member-count map (only
    PR-group descriptors get sized, sparing the identity service)."""
    project_acl_names = [names.get(d, d) for d in proj_aces]
    pr_desc: set[str] = set()
    for aces in repo_aces_by_repo.values():
        pr_desc.update(d for d in aces if _is_pr_group(names.get(d, d)))
    pr_desc.update(d for d in proj_aces if _is_pr_group(names.get(d, d)))
    counts_by_desc = _group_member_counts(sorted(pr_desc)) if pr_desc else {}
    counts_by_name: dict[str, int] = {}
    for d, cnt in counts_by_desc.items():
        nm = _norm_ident(names.get(d, d))
        counts_by_name[nm] = max(counts_by_name.get(nm, 0), cnt)
    return {"project_acl_names": project_acl_names, "counts_by_name": counts_by_name}


def _pr_groups(repos: list[dict], ado_teams: list[dict] | None,
               project_acl_names, counts_by_name: dict[str, int]) -> list[dict]:
    """PR-reviewer groups for a project, each {name, scope, members}. scope is
    'project' when the group is an ADO team or granted on the PROJECT-level Git
    token (so it applies to every repo), else 'repo' (granted only on specific
    repositories). members is exact for teams, else the group's expanded count
    (None when it couldn't be sized)."""
    found: dict[str, dict] = {}

    def put(name: str, scope: str, members):
        k = _norm_ident(name)
        short = re.split(r"[\\/]", (name or "").strip())[-1]
        cur = found.get(k)
        if cur is None:
            found[k] = {"name": short, "scope": scope, "members": members}
            return
        if scope == "project":        # project-level supersedes a repo-level sighting
            cur["scope"] = "project"
        if cur.get("members") is None and members is not None:
            cur["members"] = members

    for t in (ado_teams or []):       # ADO teams are project-scoped; members known
        if _is_pr_group(t.get("name", "")):
            put(t["name"], "project", len(t.get("members") or []))
    for name in (project_acl_names or []):
        if _is_pr_group(name):
            put(name, "project", counts_by_name.get(_norm_ident(name)))
    for r in repos:
        for a in r["acls"]:
            if _is_pr_group(a["identity"]):
                put(a["identity"], "repo", counts_by_name.get(_norm_ident(a["identity"])))
    return sorted(found.values(), key=lambda g: g["name"].lower())


def _team_validation(description: str, repos: list[dict],
                     ldap_info: dict | None,
                     ado_teams: list[dict] | None) -> dict | None:
    """Validate a project's access against its [TEAM] LDAP group.

    ADO grants access to TEAMS/GROUPS, so a repo ACL shows a group NAME, not
    the people inside it. The real 'grantees' are the MEMBERS of the ADO
    teams that hold access (plus any identity granted directly on a repo).
    We check every such PERSON against the LDAP team membership.

    `ldap_info` = {"found": bool, "members": [...]} — 'found' (the group
    exists on some server) drives ldap_resolved, NOT whether it has members,
    so a real-but-empty group isn't reported as missing."""
    team = _team_from_desc(description)
    if not team:
        return None
    ldap_info = ldap_info or {}
    members = ldap_info.get("members") or []
    ldap_found = bool(ldap_info.get("found"))
    member_keys = {_norm_ident(m["username"]) for m in members if m.get("username")} | \
                  {_norm_ident(m["display_name"]) for m in members if m.get("display_name")}
    team_key = _norm_ident(team)
    ado_teams = ado_teams or []
    team_names = {_norm_ident(t.get("name", "")) for t in ado_teams}

    # [UnAssigned]: no team owns the project — it is healthy ONLY if nobody
    # has access. Any grantee is a finding.
    if team_key == "unassigned":
        people: set[str] = set()
        for t in ado_teams:
            people.update(t.get("members") or [])
        for r in repos:
            for a in r["acls"]:
                if _norm_ident(a["identity"]) not in team_names:
                    people.add(a["identity"])
        granted = sorted(people)
        return {"team": team, "unassigned": True, "group_granted": len(granted) == 0,
                "ldap_resolved": True, "member_count": 0, "ldap_members": [],
                "granted_people": len(granted),
                "non_team_grants": granted[:100], "non_team_count": len(granted)}

    def _is_team_group(name_norm: str) -> bool:
        # the LDAP team granted as a group — the ADO identity's last segment
        # is the group name (e.g. [Proj]\Digital_Innovation -> Digital_Innovation)
        return bool(team_key) and (team_key == name_norm or team_key in name_norm)

    # people who effectively have access: ADO team members + any repo-ACL
    # identity — EXCLUDING the [TEAM] group itself in whatever form it's
    # granted (direct repo ACL, an ADO team named for it, or nested as a
    # member of an ADO team). Any of those forms means the whole team is
    # granted, so it must not be counted as an out-of-team person.
    group_granted = False
    people: set[str] = set()
    for t in ado_teams:
        if _is_team_group(_norm_ident(t.get("name", ""))):
            group_granted = True
        for m in (t.get("members") or []):
            if _is_team_group(_norm_ident(m)):
                group_granted = True   # the LDAP group nested inside the team
                continue
            people.add(m)
    for r in repos:
        for a in r["acls"]:
            idn = _norm_ident(a["identity"])
            if _is_team_group(idn):
                group_granted = True   # the LDAP group granted on the repo directly
                continue
            if idn in team_names:
                continue               # an ADO team already expanded into people
            people.add(a["identity"])

    # when the group exists but has no resolvable members, don't false-flag
    # every grantee — only flag out-of-team people when we have a member list
    non_team = (sorted(p for p in people if _norm_ident(p) not in member_keys)
                if member_keys else [])
    # DUPLICATE / redundant access: the whole team is already granted as a group,
    # yet these people (who ARE members of that team) ALSO hold an individual
    # grant — access they'd have anyway. Only meaningful when the group is
    # granted and we can resolve the team's members.
    duplicate = (sorted(p for p in people if _norm_ident(p) in member_keys)
                 if (group_granted and member_keys) else [])
    return {"team": team, "group_granted": group_granted,
            "member_count": len(members),
            "ldap_resolved": ldap_found,            # group FOUND, not "has members"
            "ldap_members": sorted(m.get("display_name") or m.get("username")
                                   for m in members)[:500],
            "granted_people": len(people),
            "non_team_grants": non_team[:100],
            "non_team_count": len(non_team),
            "duplicate_grants": duplicate[:100],
            "duplicate_count": len(duplicate)}


def _project_access_analysis(repos: list[dict], teams: list[dict],
                             description: str = "",
                             ldap_info: dict | None = None,
                             pr_ctx: dict | None = None) -> dict:
    # `teams` (with members) doubles as the grantee source for team validation
    """Is access UNIFORM across the project or REPO-SPECIFIC? Plus the many
    percentages: repo-specific %, and the identity privilege mix. `pr_ctx`
    (optional) carries {project_acl_names, counts_by_name} for PR-reviewer
    group detection."""
    total = len(repos)
    with_acls = [r for r in repos if r["acls"]]
    sigs = {r["signature"] for r in with_acls}
    # uniform = every repo that has explicit ACLs shares one identical ACL set
    uniform = len(sigs) <= 1
    members = sum(len(t["members"]) for t in teams)

    # distinct identities and their HIGHEST privilege tier across the project
    tier_of: dict[str, str] = {}
    order = {"admin": 3, "write": 2, "read": 1, "other": 0}
    for r in repos:
        for a in r["acls"]:
            cur = tier_of.get(a["identity"])
            if cur is None or order[a["tier"]] > order[cur]:
                tier_of[a["identity"]] = a["tier"]
    ids = len(tier_of)
    counts = {t: sum(1 for v in tier_of.values() if v == t)
              for t in ("admin", "write", "read", "other")}
    an = {
        "total_repos": total,
        "repos_with_explicit": len(with_acls),
        "pct_repo_specific": _pct(len(with_acls), total),
        "uniform": uniform,
        "distinct_acl_sets": len(sigs),
        "teams": len(teams), "members": members,
        "distinct_identities": ids,
        "tier_counts": counts,
        "tier_pct": {t: _pct(counts[t], ids) for t in counts},
        "pct_admin": _pct(counts["admin"], ids),
    }
    an["team_validation"] = _team_validation(description, repos, ldap_info, teams)
    # PR-reviewer groups (repo- vs project-level) + their member counts
    pr_ctx = pr_ctx or {}
    pr = _pr_groups(repos, teams, pr_ctx.get("project_acl_names") or [],
                    pr_ctx.get("counts_by_name") or {})
    an["pr_groups"] = pr
    an["pr_present"] = bool(pr)
    an["pr_scope"] = ("project" if any(g["scope"] == "project" for g in pr)
                      else "repo" if pr else None)
    an["pr_member_count"] = sum((g["members"] or 0) for g in pr)
    an["score"] = _score_project(an)
    an["grade"] = _grade(an["score"])
    return an


# ================================================================= Jira
JIRA_PROJECT_CAP = 5000  # safety bound; noted if exceeded


def _jira_holder(holder: dict, role_names: dict) -> tuple[str, str, str]:
    """(label, type, parameter). Jira DC user holders carry the internal
    'JIRAUSER…' key — surfaced so it can be flagged."""
    htype = holder.get("type", "?")
    param = str(holder.get("parameter") or holder.get("value") or "")
    if htype == "projectRole":
        return f"role {role_names.get(param, param)}", htype, param
    if htype == "group":
        return f"group {param}", htype, param
    if htype in ("user", "applicationRole"):
        return f"user {param}" if htype == "user" else f"{htype} {param}", htype, param
    return htype + (f" {param}" if param else ""), htype, param


_JIRA_USER_LOOKUP_CAP = 300  # bound direct /user lookups for keys not in a group


def _make_jira_user_resolver(jget, index: dict):
    """param (user key or username, lowercased) -> human display name. Resolves
    from the pre-built group index first (jira-users + jira-administrators
    memberships, free — already fetched), then a BOUNDED direct
    /rest/api/2/user lookup for keys granted directly but in no group (the
    flagged JIRAUSER grants). Misses memoize as '' so each key is tried once."""
    cache = dict(index)
    budget = [_JIRA_USER_LOOKUP_CAP]

    def resolve(param: str) -> str:
        if not param:
            return ""
        k = param.lower()
        if k in cache:
            return cache[k]
        name = ""
        if budget[0] > 0:
            budget[0] -= 1
            # Jira DC: JIRAUSER-keyed accounts resolve by `key`, older/local
            # accounts by `username` — try both.
            for q in ({"key": param}, {"username": param}):
                try:
                    u = jget("/rest/api/2/user", q)
                    name = (u.get("displayName") or u.get("name") or "").strip()
                    if name:
                        break
                except requests.RequestException:
                    continue
        cache[k] = name
        return name

    return resolve


def jira_permission_schemes(force: bool = False) -> dict:
    def build():
        if settings.demo_mode:
            base = "https://jira.demo"
            def slink(sid): return f"{base}/secure/admin/EditPermissionScheme!default.jspa?schemeId={sid}"
            def plink(k): return f"{base}/browse/{k}"
            schemes = [
                {"id": 1, "name": "Default Software Scheme",
                 "description": "Standard delivery-team permissions",
                 "url": slink(1),
                 "projects": [{"key": "DEVOPS", "url": plink("DEVOPS")},
                              {"key": "PLAT", "url": plink("PLAT")}],
                 "holders": [
                     {"holder": "group devops-team", "type": "group",
                      "permissions": ["Browse Projects", "Create Issues",
                                      "Edit Issues", "Transition Issues"]},
                     {"holder": "role Administrators", "type": "projectRole",
                      "permissions": ["Administer Projects", "Delete Issues"]},
                     {"holder": "user Priya Raman", "type": "user", "flag": True,
                      "key": "JIRAUSER10500", "display_name": "Priya Raman",
                      "permissions": ["Administer Projects", "Delete Issues"]},
                 ]},
                {"id": 2, "name": "Restricted Scheme",
                 "description": "Read-mostly scheme for sensitive projects",
                 "url": slink(2),
                 "projects": [{"key": "SEC", "url": plink("SEC")}],
                 "holders": [
                     {"holder": "group security-team", "type": "group",
                      "permissions": ["Browse Projects", "Administer Projects"]},
                 ]},
            ]
            # a scheme also grants a user who is NOT a jira-users member
            schemes[1]["holders"].append(
                {"holder": "user ext_contractor", "type": "user",
                 "key": "ext_contractor", "display_name": "",
                 "permissions": ["Browse Projects"], "not_member": True})
            # grouped by user, then the projects they reach (via their scheme)
            ju: dict[str, dict] = {}
            for s in schemes:
                for h in s["holders"]:
                    if not h.get("flag"):
                        continue
                    u = ju.setdefault(h["key"].lower(), {
                        "key": h.get("key", ""), "display_name": h.get("display_name", ""),
                        "holder": h["holder"], "schemes": set(), "_projects": {}})
                    u["schemes"].add(s["name"])
                    for p in s.get("projects", []):
                        u["_projects"].setdefault(p["key"],
                                                  {"key": p["key"], "url": p["url"], "scheme": s["name"]})
            flagged = [{"key": u["key"], "display_name": u["display_name"],
                        "holder": u["holder"], "schemes": sorted(u["schemes"]),
                        "projects": sorted(u["_projects"].values(), key=lambda p: p["key"]),
                        "project_count": len(u["_projects"])}
                       for u in ju.values()]
            groups = {"admin_group": "jira-administrators",
                      "users_group": "jira-users",
                      "admins": ["Alice Nasr", "Bob Farid"], "admins_count": 2,
                      "admins_readable": True,
                      "users": ["Alice Nasr", "Bob Farid", "Carol Adel",
                                "Dave Samir", "Erin Zaki"],
                      "users_count": 5, "users_readable": True}
            non_members = [{"scheme": "Restricted Scheme", "user": "ext_contractor",
                            "key": "ext_contractor", "display_name": "",
                            "in_admins": False}]
            return {"source": "demo", "schemes": schemes,
                    "jirauser_grants": flagged, "project_count": 3,
                    "groups": groups, "non_member_grants": non_members,
                    "all_projects": [{"key": "DEVOPS", "name": "Platform"},
                                     {"key": "PLAT", "name": "Control"},
                                     {"key": "SEC", "name": "Security"}]}
        if not (settings.jira_base_url and settings.jira_user):
            return {"source": "not configured", "schemes": [], "jirauser_grants": [],
                    "groups": {}, "non_member_grants": []}
        auth = (settings.jira_user, settings.jira_password)
        base = settings.jira_base_url.rstrip("/")

        def jget(path, params=None):
            r = requests.get(f"{base}{path}", params=params, auth=auth,
                             timeout=(HTTP_CONNECT, HTTP_TIMEOUT))
            r.raise_for_status()
            return r.json()

        role_names = {}
        try:
            for role in jget("/rest/api/2/role"):
                role_names[str(role.get("id"))] = role.get("name", "")
        except requests.RequestException:
            pass

        # instance-level groups: admins (shown) + jira-users (the membership
        # test — being in it = a real licensed Jira user)
        admin_members = _jira_group_members(jget, settings.jira_admin_group)
        users_members = _jira_group_members(jget, settings.jira_users_group)
        admin_keys = {m["key"].lower() for m in admin_members if m.get("key")} | \
                     {m["name"].lower() for m in admin_members if m.get("name")}
        users_keys = {m["key"].lower() for m in users_members if m.get("key")} | \
                     {m["name"].lower() for m in users_members if m.get("name")}
        # key / username -> display name, from the two group memberships we just
        # read; the resolver falls back to a direct /user lookup for the rest.
        user_index: dict[str, str] = {}
        for m in (*users_members, *admin_members):
            disp = (m.get("displayName") or m.get("name") or "").strip()
            if not disp:
                continue
            for kk in (m.get("key"), m.get("name")):
                if kk:
                    user_index.setdefault(kk.lower(), disp)
        resolve_user = _make_jira_user_resolver(jget, user_index)

        data = jget("/rest/api/2/permissionscheme", {"expand": "permissions"})
        schemes = []
        jirauser_raw = []  # (key, display, holder, scheme_id, scheme_name) per grant
        user_holders = []  # (scheme, label, param) for the membership cross-check
        for s in data.get("permissionSchemes", []):
            by_holder: dict[tuple, list[str]] = {}
            holder_meta: dict[tuple, dict] = {}
            for perm in s.get("permissions", []):
                label, htype, param = _jira_holder(perm.get("holder") or {}, role_names)
                pname = (perm.get("permission") or "").replace("_", " ").title()
                by_holder.setdefault((label, htype), []).append(pname)
                holder_meta[(label, htype)] = {"param": param}
            holders = []
            for k, v in sorted(by_holder.items()):
                param = holder_meta[k]["param"]
                is_user = k[1] == "user"
                is_ju = is_user and param.upper().startswith("JIRAUSER")
                not_member = (is_user and users_keys
                              and param.lower() not in users_keys)
                # map the internal user key/username to a real name
                display = resolve_user(param) if is_user else ""
                label = f"user {display}" if (is_user and display) else k[0]
                holders.append({"holder": label, "type": k[1],
                                "key": param if is_user else "",
                                "display_name": display,
                                "permissions": sorted(set(v)), "flag": is_ju,
                                "not_member": not_member})
                if is_ju:
                    jirauser_raw.append((param, display, label,
                                         s.get("id"), s.get("name", "")))
                if is_user:
                    user_holders.append((s.get("name", ""), label, param, display))
            schemes.append({
                "id": s.get("id"), "name": s.get("name", ""),
                "description": (s.get("description") or "")[:200],
                "url": f"{base}/secure/admin/EditPermissionScheme!default.jspa?schemeId={s.get('id')}",
                "projects": [], "holders": holders})

        # users granted in a scheme who are NOT jira-users members (can't
        # actually log in / not a licensed member) — only meaningful if we
        # could read the jira-users membership
        non_member_grants = []
        if users_keys:
            for scheme_name, label, param, display in user_holders:
                if param.lower() not in users_keys:
                    non_member_grants.append({
                        "scheme": scheme_name, "user": display or param,
                        "key": param, "display_name": display,
                        "in_admins": param.lower() in admin_keys})

        # scheme -> projects: paginate ALL projects (the 'unassigned' bug came
        # from an 80-project cap) and resolve each project's scheme in parallel
        by_id = {s["id"]: s for s in schemes}
        projects, truncated = _jira_all_projects(jget)

        def proj_scheme(p):
            try:
                return p, jget(f"/rest/api/2/project/{p['key']}/permissionscheme").get("id")
            except requests.RequestException:
                return p, None

        with ThreadPoolExecutor(max_workers=POOL) as pool:
            for p, sid in pool.map(proj_scheme, projects):
                if sid in by_id:
                    by_id[sid]["projects"].append(
                        {"key": p["key"], "url": f"{base}/browse/{p['key']}"})
        for s in schemes:
            s["projects"].sort(key=lambda x: x["key"])

        # JIRAUSER-keyed grants grouped by USER first, then the PROJECTS that
        # user reaches (via the scheme each grant is in). One row per user.
        ju_by_user: dict[str, dict] = {}
        for key, disp, holder, sid, sname in jirauser_raw:
            u = ju_by_user.setdefault(key.lower(), {
                "key": key, "display_name": disp, "holder": holder,
                "schemes": set(), "_projects": {}})
            u["schemes"].add(sname)
            for p in by_id.get(sid, {}).get("projects", []):
                u["_projects"].setdefault(p["key"],
                                          {"key": p["key"], "url": p["url"], "scheme": sname})
        jirauser_grants = []
        for u in ju_by_user.values():
            projs = sorted(u["_projects"].values(), key=lambda p: p["key"])
            jirauser_grants.append({
                "key": u["key"], "display_name": u["display_name"], "holder": u["holder"],
                "schemes": sorted(u["schemes"]), "projects": projs,
                "project_count": len(projs)})
        jirauser_grants.sort(key=lambda x: (x["display_name"] or x["key"]).lower())

        def _names(ms):
            return sorted({(m.get("displayName") or m.get("name")) for m in ms
                           if (m.get("displayName") or m.get("name"))})
        groups = {
            "admin_group": settings.jira_admin_group,
            "users_group": settings.jira_users_group,
            "admins": _names(admin_members),
            "admins_count": len(admin_members),
            "admins_readable": bool(admin_members),
            "users": _names(users_members)[:500],
            "users_count": len(users_members),
            "users_readable": bool(users_members)}
        return {"source": "live", "schemes": schemes,
                "jirauser_grants": jirauser_grants,
                "groups": groups, "non_member_grants": non_member_grants,
                "project_count": len(projects), "projects_truncated": truncated,
                "all_projects": projects}
    return _cached("jira:schemes", force, build)


def _jira_group_members(jget, group: str, cap: int = 20000) -> list[dict]:
    """Paginated members of a Jira group. Empty when the group is missing or
    the account can't read it (then the membership cross-check is skipped)."""
    if not group:
        return []
    out: list[dict] = []
    start = 0
    try:
        while len(out) < cap:
            page = jget("/rest/api/2/group/member",
                        {"groupname": group, "startAt": start,
                         "maxResults": 50, "includeInactiveUsers": "true"})
            values = page.get("values", []) if isinstance(page, dict) else []
            out.extend({"name": m.get("name", ""), "key": m.get("key", ""),
                        "displayName": m.get("displayName", ""),
                        "active": m.get("active", True)} for m in values)
            if not values or (isinstance(page, dict) and page.get("isLast")):
                break
            start += len(values)
    except requests.RequestException:
        return out
    return out


def _jira_all_projects(jget) -> tuple[list[dict], bool]:
    """Every project (key + name), paginated. Jira DC has thousands; the old
    single-call /project cap of 80 is why assigned schemes showed 'unassigned'.
    Prefers the paginated /project/search, falls back to the legacy full list."""
    out: list[dict] = []
    try:
        start = 0
        while len(out) < JIRA_PROJECT_CAP:
            page = jget("/rest/api/2/project/search",
                        {"startAt": start, "maxResults": 50})
            values = page.get("values", [])
            out.extend({"key": p["key"], "name": p.get("name", "")}
                       for p in values if p.get("key"))
            if page.get("isLast") or not values:
                return out, False
            start += len(values)
        return out, True
    except requests.RequestException:
        pass
    # legacy Jira: /project returns them all in one shot
    try:
        allp = jget("/rest/api/2/project")
        out = [{"key": p["key"], "name": p.get("name", "")}
               for p in allp if p.get("key")]
        return out[:JIRA_PROJECT_CAP], len(out) > JIRA_PROJECT_CAP
    except requests.RequestException:
        return [], False


# ============================================ Jira activity & last-seen
# each project/user costs 1-2 extra JQL calls, so this is its own lazily-loaded,
# separately-cached section (bounded so a big instance isn't hammered)
JIRA_ACTIVITY_PROJECT_CAP = 200
JIRA_ACTIVITY_USER_CAP = 300


def _jql_str(s: str) -> str:
    """A JQL string literal — quote and escape, so a key/username can't break
    out of the query."""
    return '"' + str(s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _jira_search_one(jget, jql: str, fields: str) -> dict | None:
    """The single newest issue for a JQL (already ORDER BY-ed), or None."""
    try:
        r = jget("/rest/api/2/search",
                 {"jql": jql, "maxResults": 1, "fields": fields, "validateQuery": "false"})
        issues = r.get("issues", []) if isinstance(r, dict) else []
        return issues[0] if issues else None
    except requests.RequestException:
        return None


def _issue_date(issue: dict | None, field: str) -> dict | None:
    if not issue:
        return None
    f = issue.get("fields") or {}
    if not f.get(field):
        return None
    return {"key": issue.get("key"), "date": f.get(field), "summary": f.get("summary")}


def _project_activity(jget, key: str) -> dict:
    """Last ticket OPENED (newest created) and last INTERACTION (newest updated)
    for one project — one bounded JQL search each."""
    kq = _jql_str(key)
    opened = _jira_search_one(jget, f"project = {kq} ORDER BY created DESC", "created,summary")
    inter = _jira_search_one(jget, f"project = {kq} ORDER BY updated DESC", "updated,summary")
    return {"last_opened": _issue_date(opened, "created"),
            "last_interaction": _issue_date(inter, "updated")}


def _user_last_login(jget, name: str, key: str) -> str | None:
    """Best-effort TRUE last-login. Standard Jira REST doesn't expose it (Cloud
    removed it; DC keeps it in admin/Crowd internals), but some instances/apps
    surface a loginInfo/lastLoginTime on the user object — read it if present,
    else None (the UI shows N/A)."""
    for q in ({"username": name}, {"key": key}):
        if not list(q.values())[0]:
            continue
        try:
            u = jget("/rest/api/2/user", {**q, "expand": "loginInfo,lastLoginTime"})
        except requests.RequestException:
            continue
        if not isinstance(u, dict):
            continue
        li = u.get("loginInfo") or {}
        val = li.get("lastLoginTime") or li.get("previousLoginTime") or u.get("lastLoginTime")
        if val:
            return val
    return None


def _user_last_activity(jget, name: str) -> dict | None:
    """ALWAYS-available proxy: the most recently updated issue the user reports
    or is assigned — 'when were they last active on work'."""
    if not name:
        return None
    jql = f"(reporter = {_jql_str(name)} OR assignee = {_jql_str(name)}) ORDER BY updated DESC"
    return _issue_date(_jira_search_one(jget, jql, "updated,summary"), "updated")


def _demo_jira_activity() -> dict:
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    def iso(days, hours=0):
        return (now - _dt.timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
    projects = [
        {"key": "DEVOPS", "name": "Platform",
         "last_opened": {"key": "DEVOPS-812", "date": iso(0, 3), "summary": "Pipeline flake on checkout"},
         "last_interaction": {"key": "DEVOPS-807", "date": iso(0, 1), "summary": "Rotate git credentials"}},
        {"key": "PLAT", "name": "Control",
         "last_opened": {"key": "PLAT-45", "date": iso(6), "summary": "Add team-config repo"},
         "last_interaction": {"key": "PLAT-44", "date": iso(2), "summary": "Review access policy"}},
        {"key": "SEC", "name": "Security",
         "last_opened": {"key": "SEC-9", "date": iso(140), "summary": "Quarterly access audit"},
         "last_interaction": {"key": "SEC-9", "date": iso(95), "summary": "Quarterly access audit"}},
    ]
    users = [
        {"name": "alice", "key": "JIRAUSER10001", "display_name": "Alice Nasr", "active": True,
         "last_login": iso(0, 5), "last_activity": {"key": "DEVOPS-807", "date": iso(0, 1)}},
        {"name": "bob", "key": "JIRAUSER10002", "display_name": "Bob Farid", "active": True,
         "last_login": iso(1, 2), "last_activity": {"key": "DEVOPS-812", "date": iso(0, 3)}},
        {"name": "carol", "key": "JIRAUSER10003", "display_name": "Carol Adel", "active": True,
         "last_login": None, "last_activity": {"key": "PLAT-44", "date": iso(2)}},
        {"name": "dave", "key": "JIRAUSER10004", "display_name": "Dave Samir", "active": True,
         "last_login": iso(38), "last_activity": {"key": "SEC-9", "date": iso(95)}},
        {"name": "erin", "key": "JIRAUSER10005", "display_name": "Erin Zaki", "active": False,
         "last_login": None, "last_activity": None},
    ]
    return {"source": "demo", "projects": projects, "project_total": 3,
            "projects_truncated": False, "users": users, "user_total": 5,
            "users_truncated": False, "users_readable": True, "any_login": True}


def _by_date_desc(rows: list[dict], getter) -> list[dict]:
    def key(r):
        d = getter(r)
        return d or ""   # ISO strings sort lexicographically; None -> "" (last)
    return sorted(rows, key=key, reverse=True)


def jira_activity(force: bool = False) -> dict:
    """Per-project last-opened / last-interaction dates and per-user last-login
    (best-effort) + last-activity. Lazily loaded and separately cached because
    each row costs extra JQL calls."""
    def build():
        if settings.demo_mode:
            return _demo_jira_activity()
        if not (settings.jira_base_url and settings.jira_user):
            return {"source": "not configured", "projects": [], "users": [],
                    "users_readable": False}
        auth = (settings.jira_user, settings.jira_password)
        base = settings.jira_base_url.rstrip("/")

        def jget(path, params=None):
            r = requests.get(f"{base}{path}", params=params, auth=auth,
                             timeout=(HTTP_CONNECT, HTTP_TIMEOUT))
            r.raise_for_status()
            return r.json()

        all_projects, ptrunc = _jira_all_projects(jget)
        pcap = all_projects[:JIRA_ACTIVITY_PROJECT_CAP]

        def do_proj(p):
            return {"key": p["key"], "name": p.get("name", ""),
                    "url": f"{base}/browse/{p['key']}", **_project_activity(jget, p["key"])}

        with ThreadPoolExecutor(max_workers=POOL) as pool:
            prows = list(pool.map(do_proj, pcap))
        prows = _by_date_desc(prows, lambda r: (r.get("last_interaction") or {}).get("date"))

        users_members = _jira_group_members(jget, settings.jira_users_group)
        ucap = users_members[:JIRA_ACTIVITY_USER_CAP]

        def do_user(m):
            name = m.get("name") or ""
            return {"name": name, "key": m.get("key", ""),
                    "display_name": m.get("displayName") or name,
                    "active": m.get("active", True),
                    "last_login": _user_last_login(jget, name, m.get("key", "")),
                    "last_activity": _user_last_activity(jget, name)}

        with ThreadPoolExecutor(max_workers=POOL) as pool:
            urows = list(pool.map(do_user, ucap))
        urows = _by_date_desc(urows, lambda r: (r.get("last_activity") or {}).get("date"))

        return {"source": "live", "projects": prows,
                "project_total": len(all_projects),
                "projects_truncated": ptrunc or len(all_projects) > len(pcap),
                "users": urows, "user_total": len(users_members),
                "users_truncated": len(users_members) > len(ucap),
                "users_readable": bool(users_members),
                "any_login": any(u["last_login"] for u in urows)}
    return _cached("jira:activity", force, build)


# ================================================================= Jenkins
_PERM_RE = re.compile(r"<permission>\s*([^<]+?)\s*</permission>")


def _parse_matrix_entries(xml_text: str) -> list[dict]:
    """Matrix-auth <permission> entries across plugin versions. Forms:
    'hudson.model.Item.Read:sid' (legacy), 'USER:hudson.model.Item.Read:sid'
    and 'GROUP:...:sid' (matrix-auth 2.x/3.x), and short ids like
    'Overall/Read'. The type prefix and ambiguous-sid ':' are handled."""
    grants: dict[tuple, set] = {}
    for raw in _PERM_RE.findall(xml_text):
        parts = raw.split(":")
        sid_type = "unknown"
        if parts and parts[0].upper() in ("USER", "GROUP", "EITHER") and len(parts) >= 3:
            sid_type = "group" if parts[0].upper() == "GROUP" else (
                "user" if parts[0].upper() == "USER" else "either")
            perm, sid = parts[1], ":".join(parts[2:])
        elif len(parts) >= 2:
            perm, sid = parts[0], ":".join(parts[1:])
        else:
            continue
        # 'hudson.model.Item.Read' -> 'Item/Read'; 'Overall/Read' kept as-is
        short = "/".join(perm.split(".")[-2:]) if "." in perm else perm
        grants.setdefault((sid, sid_type), set()).add(short)
    return [{"sid": sid, "type": sid_type, "permissions": sorted(perms)}
            for (sid, sid_type), perms in sorted(grants.items())]


def jenkins_matrix(force: bool = False) -> dict:
    def build():
        if settings.demo_mode:
            return {"source": "demo", "items": [
                {"path": "★ GLOBAL (instance-wide)", "entries": [
                    {"sid": "authenticated", "type": "group",
                     "permissions": ["Overall/Read"]},
                    {"sid": "devops-admins", "type": "group",
                     "permissions": ["Overall/Administer"]},
                ]},
                {"path": "(folder) payments-service", "entries": [
                    {"sid": "devops-team", "type": "group",
                     "permissions": ["Item/Build", "Item/Cancel", "Item/Read", "Item/Workspace"]},
                    {"sid": "alice", "type": "user",
                     "permissions": ["Item/Build", "Item/Configure", "Item/Delete", "Item/Read"]},
                ]},
                {"path": "platform-terraform/apply", "entries": [
                    {"sid": "platform-admins", "type": "group",
                     "permissions": ["Item/Build", "Item/Configure", "Item/Read"]},
                ]},
            ], "scanned": 3, "note": "", "global_found": True}
        from . import jenkins as jk
        if not jk.is_live():
            return {"source": "not configured", "items": [], "scanned": 0, "note": ""}
        auth = (settings.jenkins_user, settings.jenkins_token) if settings.jenkins_user else None
        items = []
        note_parts = []

        # GLOBAL strategy lives in the Jenkins ROOT config.xml — the most
        # common place grants are defined, and why per-job scans found nothing
        global_found = False
        try:
            rg = requests.get(f"{settings.jenkins_url}/config.xml", auth=auth,
                              timeout=(HTTP_CONNECT, 15))
            if rg.status_code in (401, 403):
                note_parts.append("global grants need Overall/Administer (root config.xml was "
                                  f"{rg.status_code}) — showing item-level only")
            elif rg.ok:
                gentries = _parse_matrix_entries(rg.text)
                if gentries:
                    items.append({"path": "★ GLOBAL (instance-wide)", "entries": gentries})
                    global_found = True
        except requests.RequestException:
            pass

        names = jk.all_job_names()
        # jobs + every ancestor folder, deduped, bounded
        paths: list[str] = []
        seen = set()
        for name in names:
            segs = name.split("/")
            for i in range(1, len(segs) + 1):
                p = "/".join(segs[:i])
                if p not in seen:
                    seen.add(p)
                    paths.append(p)
        capped = paths[:300]
        name_set = set(names)

        def fetch_one(p: str):
            url = settings.jenkins_url + "".join(
                f"/job/{requests.utils.quote(seg, safe='')}" for seg in p.split("/"))
            try:
                r = requests.get(f"{url}/config.xml", auth=auth,
                                 timeout=(HTTP_CONNECT, 15))
                if not r.ok:
                    return None
                entries = _parse_matrix_entries(r.text)
            except requests.RequestException:
                return None
            if not entries:
                return None
            is_folder = p not in name_set
            return {"path": ("(folder) " if is_folder else "") + p,
                    "entries": entries}

        # parallel, bounded — 300 sequential config.xml fetches was minutes
        with ThreadPoolExecutor(max_workers=POOL) as pool:
            item_results = [x for x in pool.map(fetch_one, capped) if x]
        item_results.sort(key=lambda x: x["path"].lower())
        items.extend(item_results)  # keep GLOBAL first
        if len(paths) > len(capped):
            note_parts.append(f"scanned the first {len(capped)} of {len(paths)} items")
        if not items:
            note_parts.append("no matrix entries found — if you use PROJECT-based matrix "
                              "auth, grants are per-job/folder; if GLOBAL matrix, the account "
                              "needs Overall/Administer to read the root config")
        return {"source": "live", "items": items, "scanned": len(capped),
                "note": " · ".join(note_parts), "global_found": global_found}
    return _cached("jenkins:matrix", force, build)


# ================================================================= LDAP health
def _mask_url(url: str) -> str:
    return re.sub(r"(ldaps?://)[^/@\s]+@", r"\1***@", url or "")


def ldap_health(force: bool = False) -> dict:
    """The login LDAP directory (URL only, creds masked) + a bind health check,
    plus the [TEAM]-resolution source status (the Engine repo's getTeamMembersCN.sh
    + its .prd profile) — so a dead login directory or a missing resolver asset
    is visible on the Access page."""
    def build():
        team_source = team_source_status()
        if settings.demo_mode:
            return {"servers": [
                {"url": "ldaps://ldap.demo:636", "primary": True,
                 "healthy": True, "note": "bind ok (login)"}],
                "team_source": team_source}
        servers = settings.ldap_servers
        if not servers:
            return {"servers": [], "note": "no login LDAP configured",
                    "team_source": team_source}

        def check(idx_srv):
            idx, srv = idx_srv
            row = {"url": _mask_url(srv["url"]), "primary": idx == 0,
                   "healthy": False, "note": ""}
            if not srv["url"]:
                row["note"] = "no URL"
                return row
            try:
                import ldap3
                server = ldap3.Server(srv["url"], get_info=ldap3.NONE,
                                      connect_timeout=6)
                conn = ldap3.Connection(server, user=srv["bind_dn"] or None,
                                        password=srv["bind_password"] or None,
                                        receive_timeout=6)
                if conn.bind():
                    row["healthy"], row["note"] = True, "bind ok"
                    conn.unbind()
                else:
                    row["note"] = f"bind failed: {str(conn.result.get('description', 'error'))[:60]}"
            except Exception as exc:  # noqa: BLE001
                s = str(exc).lower()
                row["note"] = ("host not found" if "resolution" in s or "not known" in s
                               else "connection refused" if "refused" in s
                               else "timed out" if "timeout" in s or "timed out" in s
                               else str(exc)[:80])
            return row

        with ThreadPoolExecutor(max_workers=POOL) as pool:
            rows = list(pool.map(check, list(enumerate(servers))))
        return {"servers": rows, "team_source": team_source}
    return _cached("ldap:health", force, build)


# ================================================================= Summary
def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", (s or "").strip().lower())


def access_summary(force: bool = False) -> dict:
    """Per-track counts + ADO/Jira same-name detection. Cheap-exact counts
    (one call per collection); named-users is a bounded best-effort."""
    def build():
        # ---- ADO: collections, projects, repos, teams, named users ----
        ado_projects_data = ado_projects(force)
        ado_names = sorted({p["name"] for p in ado_projects_data.get("projects", [])})
        ado = {"source": ado_projects_data["source"],
               "collections": len(ado_projects_data.get("collections", [])),
               "projects": len(ado_names), "repos": 0, "teams": 0,
               "named_users": 0, "approx_users": False}

        if settings.demo_mode:
            ado.update(repos=10, teams=4, named_users=4)
        elif ado_projects_data["source"] == "live":
            colls = ado_projects_data.get("collections", [])

            def coll_counts(c):
                repos = teams = 0
                users: set[str] = set()
                try:
                    repos = len(_values(_ado.coll_get(c, "/_apis/git/repositories")))
                except requests.RequestException:
                    pass
                try:
                    td = _ado.coll_get(c, "/_apis/teams",
                                       {"$top": 1000, "api-version": "6.0-preview.3"})
                    teams = len(_values(td))
                except requests.RequestException:
                    pass
                return repos, teams, users

            with ThreadPoolExecutor(max_workers=POOL) as pool:
                for repos, teams, users in pool.map(coll_counts, colls):
                    ado["repos"] += repos
                    ado["teams"] += teams
            # distinct named users across the instance (best-effort, bounded)
            ado["named_users"], ado["approx_users"] = _ado_named_users(colls)

        # ---- Jira: schemes, projects ----
        jira_data = jira_permission_schemes(force)
        jira_projects = jira_data.get("all_projects", [])
        jira = {"source": jira_data["source"],
                "schemes": len(jira_data.get("schemes", [])),
                "projects": len(jira_projects),
                "jirauser_grants": len(jira_data.get("jirauser_grants", []))}

        # ---- Jenkins ----
        jk_data = jenkins_matrix(force)
        jenkins = {"source": jk_data["source"],
                   "scopes": len(jk_data.get("items", [])),
                   "global": bool(jk_data.get("global_found"))}

        # ---- ADO vs Jira same-name detection ----
        ado_norm = {_norm(n): n for n in ado_names}
        jira_norm: dict[str, dict] = {}
        for p in jira_projects:
            for label in (p.get("name"), p.get("key")):
                if label:
                    jira_norm.setdefault(_norm(label), p)
        both, ado_only = [], []
        for k, name in sorted(ado_norm.items()):
            match = jira_norm.get(k)
            (both if match else ado_only).append(
                {"ado": name, "jira": (match or {}).get("key")} if match else name)
        matched_norms = {_norm(b["ado"]) for b in both} | {
            _norm((jira_norm.get(_norm(b["ado"])) or {}).get("key", "")) for b in both}
        jira_only = sorted({p.get("name") or p["key"] for p in jira_projects
                            if _norm(p.get("name", "")) not in ado_norm
                            and _norm(p.get("key", "")) not in ado_norm})
        overlap = {"both": both, "both_count": len(both),
                   "ado_only_count": len(ado_only), "ado_only": ado_only[:200],
                   "jira_only_count": len(jira_only), "jira_only": jira_only[:200],
                   "comparable": ado["source"] == jira["source"] != "not configured"}
        return {"ado": ado, "jira": jira, "jenkins": jenkins, "overlap": overlap}
    return _cached("access:summary", force, build)


def _ado_named_users(colls: list[str], team_cap: int = 150) -> tuple[int, bool]:
    """Distinct member display names across the instance's teams — bounded so
    a huge instance doesn't fan out into thousands of member calls."""
    teams: list[tuple[str, str]] = []  # (collection, team_id)
    for c in colls:
        try:
            for t in _values(_ado.coll_get(
                    c, "/_apis/teams",
                    {"$top": 1000, "api-version": "6.0-preview.3"})):
                if t:
                    teams.append((c, t.get("id", ""), t.get("projectId", "")))
        except requests.RequestException:
            continue
    capped = teams[:team_cap]
    users: set[str] = set()

    def members(entry):
        c, tid, pid = entry
        try:
            data = _ado.coll_get(c, f"/_apis/projects/{pid}/teams/{tid}/members",
                                 {"$top": 500})
            return [(m.get("identity") or m).get("displayName", "")
                    for m in _values(data) if m]
        except requests.RequestException:
            return []

    with ThreadPoolExecutor(max_workers=POOL) as pool:
        for names in pool.map(members, capped):
            for n in names:
                if n and not _is_service_account(n):
                    users.add(n)
    return len(users), len(teams) > team_cap
