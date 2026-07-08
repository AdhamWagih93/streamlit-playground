from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...auth.rbac import User
from .scope import visible_apps
from .world import ENVS, TEAMS, get_world


def summary(user: User) -> dict:
    w = get_world()
    apps = visible_apps(user)
    names = {a.application for a in apps}
    pipelines = sum(1 for r in w.ado["repos"] if r["pipelined"] and r["app"] in names)
    fails_24h = sum(
        1 for e in w.events
        if e["type"] == "deploy" and e["status"] == "failed" and e["app"] in names
        and e["when"] > datetime.now(timezone.utc) - timedelta(hours=24)
    )
    live_prd = sum(1 for a in apps if a.stages.get("prd"))
    return {
        "applications": len(apps),
        "pipelines": pipelines,
        "teams": len(TEAMS) if user.is_admin else len(user.teams),
        "environments": len(ENVS),
        "live_in_prd": live_prd,
        "failed_deploys_24h": fails_24h,
        "open_incidents": sum(1 for i in w.incidents if i["status"] == "open"
                              and i["app"] in names),
        "projects": len({a.project for a in apps}),
        "companies": len({a.company for a in apps}),
    }


def recent_events(user: User, limit: int = 30) -> list[dict]:
    names = {a.application for a in visible_apps(user)}
    types = set(user.visible_event_types)
    out = []
    for e in get_world().events:
        base = "build-develop" if e["type"] == "build-develop" else \
               "build-release" if e["type"] == "build-release" else e["type"]
        gate = base if base in ("deploy", "release", "request", "commit") else base
        if e["app"] not in names or gate not in types:
            continue
        out.append({**e, "when": e["when"].isoformat()})
        if len(out) >= limit:
            break
    return out
