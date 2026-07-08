"""Delivery Fleet (pipelines inventory) — demo provider.

Everything derives from the seeded world; every function is RBAC-scoped through
scope.visible_apps / User.can_see_row so the router stays thin.
"""
from __future__ import annotations

import math
from collections import Counter

from ...auth.rbac import User
from .scope import app_by_name, visible_apps
from .world import ENVS, STAGES, App, get_world

SCANNERS = ["prismacloud", "invicti", "zap", "trufflehog"]
SEVERITIES = ["critical", "high", "medium", "low"]
SORTS = {"name", "activity", "vuln", "prd", "live"}


# ------------------------------------------------------------------ helpers
def _latest_when(app: App) -> str:
    """ISO timestamp of the most recent stage activity ('' when no stages)."""
    return max((s.get("when", "") for s in app.stages.values() if s), default="")


def _latest_version(app: App) -> str:
    """Version carried by the most recently touched stage."""
    best_when, best_ver = "", ""
    for s in app.stages.values():
        if s and s.get("when", "") >= best_when:
            best_when, best_ver = s.get("when", ""), s.get("version", "")
    return best_ver


def _vuln_profile(app: App, scans: dict) -> tuple[bool, int]:
    """(has_critical, critical+high total) across scanners for the latest version."""
    ver = _latest_version(app)
    has_crit, total = False, 0
    if ver:
        for scanner in SCANNERS:
            c = scans.get((scanner, app.application, ver))
            if not c:
                continue
            if c.get("critical", 0) > 0:
                has_crit = True
            total += c.get("critical", 0) + c.get("high", 0)
    return has_crit, total


def _row(app: App, scans: dict) -> dict:
    has_crit, vuln = _vuln_profile(app, scans)
    return {
        "application": app.application,
        "project": app.project,
        "company": app.company,
        "app_type": app.app_type,
        "build_technology": app.build_technology,
        "deploy_technology": app.deploy_technology,
        "deploy_platform": app.deploy_platform,
        "teams": app.teams,
        "stages": {st: app.stages[st] for st in STAGES if app.stages.get(st)},
        "next_versions": app.next_versions,
        "is_legacy": app.is_legacy,
        "has_critical": has_crit,
        "prd_live": bool(app.stages.get("prd")),
        "_vuln": vuln,
        "_activity": _latest_when(app),
    }


# ------------------------------------------------------------------ endpoints
def list_inventory(user: User, q: str = "", projects: str = "", company: str = "",
                   app_type: str = "", technology: str = "", platform: str = "",
                   sort: str = "name", page: int = 1, size: int = 50) -> dict:
    w = get_world()
    apps = visible_apps(user)

    ql = (q or "").strip().lower()
    if ql:
        apps = [a for a in apps if ql in a.application.lower()]
    pset = {p.strip() for p in (projects or "").split(",") if p.strip()}
    if pset:
        apps = [a for a in apps if a.project in pset]
    if company:
        apps = [a for a in apps if a.company == company]
    if app_type:
        apps = [a for a in apps if a.app_type == app_type]
    if technology:
        apps = [a for a in apps if technology in (a.build_technology, a.deploy_technology)]
    if platform:
        apps = [a for a in apps if a.deploy_platform == platform]

    rows = [_row(a, w.scans) for a in apps]
    sort = sort if sort in SORTS else "name"
    if sort == "name":
        rows.sort(key=lambda r: r["application"])
    elif sort == "activity":
        rows.sort(key=lambda r: r["_activity"], reverse=True)
    elif sort == "vuln":
        rows.sort(key=lambda r: (-r["_vuln"], r["application"]))
    elif sort == "prd":
        rows.sort(key=lambda r: (r["stages"].get("prd") or {}).get("when", ""), reverse=True)
    elif sort == "live":
        rows.sort(key=lambda r: (not r["prd_live"], r["application"]))

    size = max(1, min(int(size or 50), 200))
    total = len(rows)
    pages = max(1, math.ceil(total / size))
    page = max(1, min(int(page or 1), pages))
    for r in rows:
        r.pop("_vuln", None)
        r.pop("_activity", None)
    return {"rows": rows[(page - 1) * size: page * size],
            "total": total, "page": page, "pages": pages}


def facets(user: User) -> dict:
    w = get_world()
    apps = visible_apps(user)
    proj_counts = Counter(a.project for a in apps)
    with_critical = sum(1 for a in apps if _vuln_profile(a, w.scans)[0])
    return {
        "projects": [{"name": p, "count": c} for p, c in sorted(proj_counts.items())],
        "companies": sorted({a.company for a in apps if a.company}),
        "app_types": sorted({a.app_type for a in apps if a.app_type}),
        "technologies": sorted({a.build_technology for a in apps if a.build_technology}
                               | {a.deploy_technology for a in apps if a.deploy_technology}),
        "platforms": sorted({a.deploy_platform for a in apps if a.deploy_platform}),
        "stats": {
            "apps": len(apps),
            "live_prd": sum(1 for a in apps if a.stages.get("prd")),
            "with_critical": with_critical,
            "projects": len(proj_counts),
        },
    }


def app_detail(user: User, project: str, application: str) -> dict | None:
    """Full app detail; None when the app doesn't exist OR isn't visible (→ router 404,
    deliberately indistinguishable so scoping never leaks existence)."""
    app = app_by_name(project, application)
    if app is None or not user.can_see_row(app.teams):
        return None
    w = get_world()

    build_ver = (app.stages.get("build") or {}).get("version") or _latest_version(app)

    def image(name: str, tag: str) -> str:
        if not name:
            return ""
        t = (tag or "").replace("{version}", build_ver or "latest")
        return f"{name}:{t}" if t else name

    identity = {
        "application": app.application,
        "project": app.project,
        "company": app.company,
        "app_type": app.app_type,
        "build_technology": app.build_technology,
        "deploy_technology": app.deploy_technology,
        "deploy_platform": app.deploy_platform,
        "repository": app.repository_name,
        "repo_url": f"http://ado.corp/{app.company}/{app.project}/_git/{app.repository_name}",
        "build_image": image(app.build_image_name, app.build_image_tag),
        "deploy_image": image(app.deploy_image_name, app.deploy_image_tag),
        "namespaces": app.namespaces,
        "teams": app.teams,
        "is_legacy": app.is_legacy,
    }

    stages = [{"stage": st, **(app.stages.get(st)
                               or {"version": "", "status": "", "when": "", "by": ""})}
              for st in STAGES]

    recent_deploys = [
        {"env": e["env"], "version": e["version"], "status": e["status"],
         "when": e["when"].isoformat(), "user": e["user"], "reason": e.get("reason", "")}
        for e in w.events
        if e["type"] == "deploy" and e["app"] == app.application
    ][:8]  # events are pre-sorted newest first

    prd_ver = (app.stages.get("prd") or {}).get("version", "")
    security = []
    for scanner in SCANNERS:
        prd_counts = w.scans.get((scanner, app.application, prd_ver)) if prd_ver else None
        envs: dict[str, dict] = {}
        for env in ENVS:
            st = app.stages.get(env)
            if not st or not st.get("version"):
                continue
            counts = w.scans.get((scanner, app.application, st["version"]))
            if not counts:
                continue
            entry = {"version": st["version"],
                     "counts": {s: counts.get(s, 0) for s in SEVERITIES}}
            if prd_counts:
                entry["delta_vs_prd"] = {s: counts.get(s, 0) - prd_counts.get(s, 0)
                                         for s in SEVERITIES}
            envs[env] = entry
        security.append({"scanner": scanner, "envs": envs})

    by_type = Counter(e["type"] for e in w.events if e["app"] == app.application)
    stats = {
        "commits": by_type.get("commit", 0),
        "builds": by_type.get("build-develop", 0) + by_type.get("build-release", 0),
        "releases": by_type.get("release", 0),
        "deploys": by_type.get("deploy", 0),
        "jira": by_type.get("request", 0),  # demo world proxies jira issues as requests
    }

    return {
        "identity": identity,
        "stages": stages,
        "next_versions": app.next_versions,
        "recent_deploys": recent_deploys,
        "security": security,
        "stats": stats,
        "prd_live": bool(app.stages.get("prd")),
    }
