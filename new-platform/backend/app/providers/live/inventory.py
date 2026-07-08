"""Delivery Fleet (pipelines inventory) — live provider (Elasticsearch).

Mirrors app.providers.demo.inventory signatures exactly. Data sources:
  * rows/identity  — ef-devops-inventory
  * stage latest   — ef-cicd-builds / ef-cicd-releases / ef-cicd-deployments
                     (terms agg by application + top_hits newest-first,
                      deployments filtered by environment, success statuses only)
  * scan counts    — ef-cicd-prismacloud / invicti / zap / trufflehog
                     (Vcritical/Vhigh/Vmedium/Vlow, agg app→codeversion→latest)

RBAC is applied in Python after fetch (User.can_see_row over *_team fields),
identical to demo. Queries are defensive: a missing index/field degrades to
"no data for that facet"; only an unreachable cluster raises IntegrationUnavailable.
"""
from __future__ import annotations

import math
from collections import Counter

from ...auth.rbac import ALL_TEAM_FIELDS, User
from .clients import IDX, IntegrationUnavailable, es

ENVS = ["dev", "qc", "uat", "prd"]
STAGES = ["build", "release", "dev", "qc", "uat", "prd"]
SCANNERS = ["prismacloud", "invicti", "zap", "trufflehog"]
SEVERITIES = ["critical", "high", "medium", "low"]
SEV_FIELDS = {"critical": "Vcritical", "high": "Vhigh", "medium": "Vmedium", "low": "Vlow"}
SUCCESS = ["SUCCESS", "Success", "success", "ok", "Succeeded"]
SORTS = {"name", "activity", "vuln", "prd", "live"}


# ------------------------------------------------------------------ ES helpers
def _search(index: str, body: dict, required: bool = False) -> dict | None:
    """Run a search; optional data sources degrade to None instead of failing."""
    try:
        client = es()
    except IntegrationUnavailable:
        raise
    try:
        return client.search(index=index, body=body, ignore_unavailable=True)
    except Exception as exc:
        if required:
            raise IntegrationUnavailable("Elasticsearch", f"query on {index} failed: {exc}")
        return None


def _field_terms(field: str, values: list[str]) -> dict:
    """Match either a keyword mapping or a text mapping without knowing which."""
    return {"bool": {"minimum_should_match": 1, "should": [
        {"terms": {field: values}},
        {"terms": {f"{field}.keyword": values}},
        {"terms": {field: [v.lower() for v in values]}},
    ]}}


def _as_list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x]
    return [str(v)]


def _fetch_inventory() -> list[dict]:
    res = _search(IDX["inventory"], {"size": 10000, "query": {"match_all": {}}},
                  required=True)
    out = []
    for h in (res or {}).get("hits", {}).get("hits", []):
        src = h.get("_source") or {}
        application = src.get("application")
        if not application:
            continue  # defensive: skip malformed docs
        out.append({
            "application": str(application),
            "project": str(src.get("project") or ""),
            "company": str(src.get("company") or ""),
            "app_type": str(src.get("app_type") or ""),
            "build_technology": str(src.get("build_technology") or ""),
            "deploy_technology": str(src.get("deploy_technology") or ""),
            "deploy_platform": str(src.get("deploy_platform") or ""),
            "repository_name": str(src.get("repository_name") or application),
            "build_image": src.get("build_image") or {},
            "deploy_image": src.get("deploy_image") or {},
            "namespaces": src.get("namespaces") or {},
            "is_legacy": bool(src.get("is_legacy", False)),
            "teams": {f: _as_list(src.get(f)) for f in ALL_TEAM_FIELDS},
        })
    return out


def _latest_hits(index: str, env: str | None = None,
                 date_field: str = "date") -> dict[str, dict]:
    """application -> newest successful {version,status,when,by} from a CICD index."""
    filters: list[dict] = [_field_terms("status", SUCCESS)]
    if env:
        filters.append(_field_terms("environment", [env]))
    body = {
        "size": 0,
        "query": {"bool": {"filter": filters}},
        "aggs": {"apps": {
            "terms": {"field": "application.keyword", "size": 5000},
            "aggs": {"latest": {"top_hits": {
                "size": 1,
                "sort": [{date_field: {"order": "desc", "unmapped_type": "date"}}],
            }}},
        }},
    }
    res = _search(index, body)
    out: dict[str, dict] = {}
    for b in ((res or {}).get("aggregations", {}).get("apps", {}).get("buckets", [])):
        hits = b.get("latest", {}).get("hits", {}).get("hits", [])
        if not hits:
            continue
        src = hits[0].get("_source") or {}
        out[str(b.get("key"))] = {
            "version": str(src.get("version") or src.get("codeversion") or ""),
            "status": "ok",
            "when": str(src.get(date_field) or ""),
            "by": str(src.get("user") or src.get("username")
                      or src.get("triggered_by") or ""),
        }
    return out


def _stage_maps() -> dict[str, dict[str, dict]]:
    """stage -> {application -> stage info} for all six stages."""
    maps = {
        "build": _latest_hits(IDX["builds"]),
        "release": _latest_hits(IDX["releases"]),
    }
    for env in ENVS:
        maps[env] = _latest_hits(IDX["deployments"], env=env)
    return maps


def _scan_map(scanner: str) -> dict[tuple[str, str], dict]:
    """(application, version) -> latest severity counts for one scanner."""
    body = {
        "size": 0,
        "aggs": {"apps": {
            "terms": {"field": "application.keyword", "size": 5000},
            "aggs": {"versions": {
                "terms": {"field": "codeversion.keyword", "size": 100},
                "aggs": {"latest": {"top_hits": {
                    "size": 1,
                    "sort": [{"enddate": {"order": "desc", "unmapped_type": "date"}}],
                }}},
            }},
        }},
    }
    res = _search(IDX[scanner], body)
    out: dict[tuple[str, str], dict] = {}
    for a in ((res or {}).get("aggregations", {}).get("apps", {}).get("buckets", [])):
        app = str(a.get("key"))
        for v in a.get("versions", {}).get("buckets", []):
            hits = v.get("latest", {}).get("hits", {}).get("hits", [])
            if not hits:
                continue
            src = hits[0].get("_source") or {}
            counts = {}
            for sev, f in SEV_FIELDS.items():
                try:
                    counts[sev] = int(src.get(f) or 0)
                except (TypeError, ValueError):
                    counts[sev] = 0
            counts["status"] = "ok"
            counts["when"] = str(src.get("enddate") or "")
            out[(app, str(v.get("key")))] = counts
    return out


def _all_scans() -> dict[str, dict[tuple[str, str], dict]]:
    return {scanner: _scan_map(scanner) for scanner in SCANNERS}


# ------------------------------------------------------------------ row shaping
def _latest_when(stages: dict) -> str:
    return max((s.get("when", "") for s in stages.values() if s), default="")


def _latest_version(stages: dict) -> str:
    best_when, best_ver = "", ""
    for s in stages.values():
        if s and s.get("when", "") >= best_when:
            best_when, best_ver = s.get("when", ""), s.get("version", "")
    return best_ver


def _vuln_profile(app: str, stages: dict, scans: dict) -> tuple[bool, int]:
    ver = _latest_version(stages)
    has_crit, total = False, 0
    if ver:
        for scanner in SCANNERS:
            c = scans.get(scanner, {}).get((app, ver))
            if not c:
                continue
            if c.get("critical", 0) > 0:
                has_crit = True
            total += c.get("critical", 0) + c.get("high", 0)
    return has_crit, total


def _row(doc: dict, stage_maps: dict, scans: dict) -> dict:
    app = doc["application"]
    stages = {st: stage_maps[st][app] for st in STAGES if app in stage_maps.get(st, {})}
    has_crit, vuln = _vuln_profile(app, stages, scans)
    return {
        "application": app,
        "project": doc["project"],
        "company": doc["company"],
        "app_type": doc["app_type"],
        "build_technology": doc["build_technology"],
        "deploy_technology": doc["deploy_technology"],
        "deploy_platform": doc["deploy_platform"],
        "teams": doc["teams"],
        "stages": stages,
        "next_versions": {},  # populated per-app in app_detail (versions lookup)
        "is_legacy": doc["is_legacy"],
        "has_critical": has_crit,
        "prd_live": bool(stages.get("prd")),
        "_vuln": vuln,
        "_activity": _latest_when(stages),
    }


def _visible_docs(user: User) -> list[dict]:
    return [d for d in _fetch_inventory() if user.can_see_row(d["teams"])]


# ------------------------------------------------------------------ endpoints
def list_inventory(user: User, q: str = "", projects: str = "", company: str = "",
                   app_type: str = "", technology: str = "", platform: str = "",
                   sort: str = "name", page: int = 1, size: int = 50) -> dict:
    docs = _visible_docs(user)

    ql = (q or "").strip().lower()
    if ql:
        docs = [d for d in docs if ql in d["application"].lower()]
    pset = {p.strip() for p in (projects or "").split(",") if p.strip()}
    if pset:
        docs = [d for d in docs if d["project"] in pset]
    if company:
        docs = [d for d in docs if d["company"] == company]
    if app_type:
        docs = [d for d in docs if d["app_type"] == app_type]
    if technology:
        docs = [d for d in docs
                if technology in (d["build_technology"], d["deploy_technology"])]
    if platform:
        docs = [d for d in docs if d["deploy_platform"] == platform]

    stage_maps = _stage_maps()
    scans = _all_scans()
    rows = [_row(d, stage_maps, scans) for d in docs]

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
    docs = _visible_docs(user)
    stage_maps = _stage_maps()
    scans = _all_scans()
    proj_counts = Counter(d["project"] for d in docs if d["project"])
    live_prd = with_critical = 0
    for d in docs:
        app = d["application"]
        stages = {st: stage_maps[st][app] for st in STAGES
                  if app in stage_maps.get(st, {})}
        if stages.get("prd"):
            live_prd += 1
        if _vuln_profile(app, stages, scans)[0]:
            with_critical += 1
    return {
        "projects": [{"name": p, "count": c} for p, c in sorted(proj_counts.items())],
        "companies": sorted({d["company"] for d in docs if d["company"]}),
        "app_types": sorted({d["app_type"] for d in docs if d["app_type"]}),
        "technologies": sorted({d["build_technology"] for d in docs if d["build_technology"]}
                               | {d["deploy_technology"] for d in docs if d["deploy_technology"]}),
        "platforms": sorted({d["deploy_platform"] for d in docs if d["deploy_platform"]}),
        "stats": {
            "apps": len(docs),
            "live_prd": live_prd,
            "with_critical": with_critical,
            "projects": len(proj_counts),
        },
    }


def _next_versions(application: str) -> dict[str, str]:
    """branch -> next version from the versions lookup; {} when unavailable."""
    body = {"size": 50,
            "query": {"bool": {"filter": [_field_terms("application", [application])]}}}
    res = _search(IDX["versions"], body)
    out: dict[str, str] = {}
    for h in ((res or {}).get("hits", {}).get("hits", [])):
        src = h.get("_source") or {}
        branch = src.get("branch") or src.get("branch_name")
        nxt = src.get("next_version") or src.get("nextversion") or src.get("version")
        if branch and nxt:
            out[str(branch)] = str(nxt)
    return out


def _recent_deploys(application: str) -> list[dict]:
    body = {
        "size": 8,
        "query": {"bool": {"filter": [_field_terms("application", [application])]}},
        "sort": [{"date": {"order": "desc", "unmapped_type": "date"}}],
    }
    res = _search(IDX["deployments"], body)
    out = []
    for h in ((res or {}).get("hits", {}).get("hits", [])):
        src = h.get("_source") or {}
        raw = str(src.get("status") or "")
        out.append({
            "env": str(src.get("environment") or ""),
            "version": str(src.get("version") or src.get("codeversion") or ""),
            "status": "ok" if raw in SUCCESS else "failed",
            "when": str(src.get("date") or ""),
            "user": str(src.get("user") or src.get("username") or ""),
            "reason": str(src.get("reason") or ""),
        })
    return out


def _count(index: str, application: str) -> int:
    body = {"size": 0, "track_total_hits": True,
            "query": {"bool": {"filter": [_field_terms("application", [application])]}}}
    res = _search(index, body)
    total = ((res or {}).get("hits", {}).get("total") or {})
    return int(total.get("value", 0)) if isinstance(total, dict) else int(total or 0)


def app_detail(user: User, project: str, application: str) -> dict | None:
    doc = next((d for d in _fetch_inventory()
                if d["application"] == application and d["project"] == project), None)
    if doc is None or not user.can_see_row(doc["teams"]):
        return None  # router converts to 404 — no existence leaks

    stage_maps = _stage_maps()
    stages_present = {st: stage_maps[st][application] for st in STAGES
                      if application in stage_maps.get(st, {})}
    build_ver = (stages_present.get("build") or {}).get("version") \
        or _latest_version(stages_present)

    def image(img: dict) -> str:
        name = str((img or {}).get("name") or "")
        if not name:
            return ""
        tag = str((img or {}).get("tag") or "").replace("{version}", build_ver or "latest")
        return f"{name}:{tag}" if tag else name

    identity = {
        "application": application,
        "project": doc["project"],
        "company": doc["company"],
        "app_type": doc["app_type"],
        "build_technology": doc["build_technology"],
        "deploy_technology": doc["deploy_technology"],
        "deploy_platform": doc["deploy_platform"],
        "repository": doc["repository_name"],
        "repo_url": f"http://ado.corp/{doc['company']}/{doc['project']}/_git/{doc['repository_name']}",
        "build_image": image(doc["build_image"]),
        "deploy_image": image(doc["deploy_image"]),
        "namespaces": doc["namespaces"],
        "teams": doc["teams"],
        "is_legacy": doc["is_legacy"],
    }

    stages = [{"stage": st, **(stages_present.get(st)
                               or {"version": "", "status": "", "when": "", "by": ""})}
              for st in STAGES]

    scans = _all_scans()
    prd_ver = (stages_present.get("prd") or {}).get("version", "")
    security = []
    for scanner in SCANNERS:
        smap = scans.get(scanner, {})
        prd_counts = smap.get((application, prd_ver)) if prd_ver else None
        envs: dict[str, dict] = {}
        for env in ENVS:
            st = stages_present.get(env)
            if not st or not st.get("version"):
                continue
            counts = smap.get((application, st["version"]))
            if not counts:
                continue
            entry = {"version": st["version"],
                     "counts": {s: counts.get(s, 0) for s in SEVERITIES}}
            if prd_counts:
                entry["delta_vs_prd"] = {s: counts.get(s, 0) - prd_counts.get(s, 0)
                                         for s in SEVERITIES}
            envs[env] = entry
        security.append({"scanner": scanner, "envs": envs})

    stats = {
        "commits": _count(IDX["commits"], application),
        "builds": _count(IDX["builds"], application),
        "releases": _count(IDX["releases"], application),
        "deploys": _count(IDX["deployments"], application),
        "jira": _count(IDX["jira"], application),
    }

    return {
        "identity": identity,
        "stages": stages,
        "next_versions": _next_versions(application),
        "recent_deploys": _recent_deploys(application),
        "security": security,
        "stats": stats,
        "prd_live": bool(stages_present.get("prd")),
    }
