"""Governance — sync checks, ADO coverage, history→PG migration, tool-access audit."""
from __future__ import annotations

from datetime import datetime, timezone

from ...auth.rbac import ALL_TEAM_FIELDS, User, team_match_key
from .world import get_world


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ sync: git↔ES
def sync_inventory(user: User) -> dict:
    w = get_world()
    d = w.drift["inventory_es"]
    return dict(
        git_total=len(w.apps) + len(d["only_git"]),
        es_total=len(w.apps) + len(d["only_es"]),
        in_both=len(w.apps),
        only_git=d["only_git"],
        only_es=d["only_es"],
        field_diffs=d["field_diffs"],
        last_run=d["last_run"],
    )


def sync_inventory_run(user: User) -> dict:
    get_world().drift["inventory_es"]["last_run"] = _now_iso()
    return sync_inventory(user)


# ------------------------------------------------------------------ sync: inv↔PG
def sync_postgres(user: User) -> dict:
    w = get_world()
    d = w.drift["postgres"]
    inv_projects = len({a.project for a in w.apps})
    return dict(
        inventory_projects=inv_projects,
        postgres_projects=inv_projects - len(d["only_inventory"]) + len(d["only_postgres"]),
        only_inventory=d["only_inventory"],
        only_postgres=d["only_postgres"],
        team_diffs=d["team_diffs"],
        ops_inconsistent=d["ops_inconsistent"],
        last_run=d["last_run"],
    )


def sync_postgres_run(user: User) -> dict:
    get_world().drift["postgres"]["last_run"] = _now_iso()
    return sync_postgres(user)


# ------------------------------------------------------------------ sync: LDAP
def sync_ldap(user: User) -> dict:
    return dict(get_world().drift["ldap"])


def sync_ldap_run(user: User) -> dict:
    d = get_world().drift["ldap"]
    d["last_sync"] = _now_iso()
    d["status"] = "success"
    return sync_ldap(user)


# ------------------------------------------------------------------ ADO coverage
def ado_coverage(user: User) -> dict:
    w = get_world()
    required = w.ado["required_hooks"]
    apps_by_name = {a.application: a for a in w.apps}
    app_repos = [r for r in w.ado["repos"] if r["app"]]
    orphans = [r["repo"] for r in w.ado["repos"] if r["app"] is None]

    pipelined = [r for r in app_repos if r["pipelined"]]
    no_repo = [dict(app=r["app"], project=r["project"], repo=r["repo"])
               for r in app_repos if not r["pipelined"]]

    missing_hooks = []
    hooks_complete = 0
    for r in pipelined:
        missing = [h for h in required if h not in r["hooks"]]
        if missing:
            missing_hooks.append(dict(app=r["app"], project=r["project"],
                                      hooks_present=r["hooks"], hooks_missing=missing))
        else:
            hooks_complete += 1

    team_mismatch = []
    for r in app_repos:
        app = apps_by_name.get(r["app"])
        inv_team = ((app.teams.get("dev_team") or [""])[0]) if app else ""
        if inv_team and team_match_key(inv_team) != team_match_key(r["ado_team"]):
            team_mismatch.append(dict(app=r["app"], project=r["project"],
                                      inventory_team=inv_team, ado_team=r["ado_team"]))

    azure_pipelines = sorted(r["app"] for r in app_repos if r["azure_pipeline"])
    apps_total = len(w.apps)
    return dict(
        headline=dict(
            apps_total=apps_total,
            pipelined=len(pipelined),
            pct=round(100 * len(pipelined) / max(1, apps_total), 1),
        ),
        tiles=dict(
            pipelined=len(pipelined),
            no_repo=len(no_repo),
            hooks_complete=hooks_complete,
            missing_hooks=len(missing_hooks),
            team_mismatch=len(team_mismatch),
            azure_pipelines=len(azure_pipelines),
        ),
        required_hooks=required,
        no_repo=sorted(no_repo, key=lambda x: (x["project"], x["app"])),
        missing_hooks=sorted(missing_hooks, key=lambda x: (x["project"], x["app"])),
        team_mismatch=sorted(team_mismatch, key=lambda x: (x["project"], x["app"])),
        azure_pipelines=azure_pipelines,
        orphans=sorted(orphans),
    )


# ------------------------------------------------------------------ history → PG
def _history_payload() -> dict:
    w = get_world()
    jobs = [dict(j) for j in w.history_jobs.values()]
    total = sum(j["total"] for j in jobs)
    migrated = sum(j["migrated"] for j in jobs)
    statuses = [j["status"] for j in jobs]
    return dict(
        jobs=jobs,
        rollup=dict(
            es_total_docs=total,
            migrated_docs=migrated,
            pct=round(100 * migrated / max(1, total), 1),
            running=statuses.count("running"),
            paused=statuses.count("paused"),
            done=statuses.count("done"),
            idle=statuses.count("idle"),
        ),
    )


def history(user: User) -> dict:
    return _history_payload()


def history_action(user: User, index_key: str, action: str) -> dict | None:
    w = get_world()
    job = w.history_jobs.get(index_key)
    if job is None or action not in ("start", "pause", "resume", "sync_new"):
        return None
    if action in ("start", "resume"):
        job["status"] = "running"
    elif action == "pause":
        job["status"] = "paused"
    elif action == "sync_new":
        job["migrated"] = int(job["total"] * 0.9)
        job["status"] = "running"
    job["updated"] = _now_iso()
    return _history_payload()


def history_tick(user: User) -> dict:
    w = get_world()
    for job in w.history_jobs.values():
        if job["status"] != "running":
            continue
        job["migrated"] = min(job["total"], job["migrated"] + max(1, int(job["total"] * 0.08)))
        if job["migrated"] >= job["total"]:
            job["status"] = "done"
        job["updated"] = _now_iso()
    return _history_payload()


# ------------------------------------------------------------------ tool access
def tool_access(user: User) -> dict:
    w = get_world()
    # project → owning teams (union of every *_team field of that project's apps)
    owners: dict[str, dict[str, str]] = {}
    for a in w.apps:
        bucket = owners.setdefault(a.project, {})
        for f in ALL_TEAM_FIELDS:
            for t in a.teams.get(f, []) or []:
                bucket[team_match_key(t)] = t

    grants = [g for g in w.tool_access if g["is_active"]]
    unauthorized = []
    for g in grants:
        if g["tool"] not in ("ADO", "JIRA"):
            continue
        own = owners.get(g["project"], {})
        if team_match_key(g["team"]) not in own:
            names = ", ".join(sorted(set(own.values()))) or "nobody"
            unauthorized.append(dict(
                user=g["user"], email=g["email"], team=g["team"], tool=g["tool"],
                project=g["project"], privilege=g["privilege"],
                last_updated=g["last_updated"],
                why=f"team {g['team']} does not own project {g['project']} — owners: {names}",
            ))
    unauthorized.sort(key=lambda x: (x["project"], x["user"]))

    breakdown_map: dict[str, dict] = {}
    for g in grants:
        row = breakdown_map.setdefault(g["project"], dict(
            project=g["project"], ADO=0, JIRA=0, Jenkins=0, total=0))
        row[g["tool"]] += 1
        row["total"] += 1
    breakdown = sorted(breakdown_map.values(), key=lambda x: (-x["total"], x["project"]))

    rbac_checked = sum(1 for g in grants if g["tool"] in ("ADO", "JIRA"))
    return dict(
        tiles=dict(
            active_grants=len(grants),
            users=len({g["email"] for g in grants}),
            ado=sum(1 for g in grants if g["tool"] == "ADO"),
            jira=sum(1 for g in grants if g["tool"] == "JIRA"),
            jenkins=sum(1 for g in grants if g["tool"] == "Jenkins"),
            rbac_checked=rbac_checked,
            unauthorized=len(unauthorized),
        ),
        unauthorized=unauthorized,
        breakdown=breakdown,
    )
