"""Security posture — live provider.

Summary/drill-down aggregate the scanner indices (ef-cicd-prismacloud / -invicti /
-zap / -trufflehog: fields Vcritical/Vhigh/Vmedium/Vlow, application, codeversion,
enddate) with application → codeversion → top_hits(enddate desc) aggs. RBAC scoping
happens in Python against the inventory index team fields — never in the query, so
a crafted client filter can't widen visibility. Reports are proxied from S3.
"""
from __future__ import annotations

import html as html_mod
import math

from fastapi import HTTPException

from ...auth.rbac import ALL_TEAM_FIELDS, User
from ...config import get_settings
from .clients import IDX, IntegrationUnavailable, es, s3_client

SCANNERS = ["prismacloud", "invicti", "zap", "trufflehog"]
SEVERITIES = ["critical", "high", "medium", "low"]
SEV_FIELDS = {"critical": "Vcritical", "high": "Vhigh", "medium": "Vmedium", "low": "Vlow"}
ENVS = ["dev", "qc", "uat", "prd"]


def _kept_severities(floor: str) -> list[str]:
    if floor not in SEVERITIES:
        floor = "low"
    return SEVERITIES[: SEVERITIES.index(floor) + 1]


# ------------------------------------------------------------------ inventory / RBAC
def _teams_of(src: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in ALL_TEAM_FIELDS:
        v = src.get(f)
        if isinstance(v, str):
            v = [v]
        out[f] = [x for x in (v or []) if x]
    return out


def _visible_inventory(user: User) -> dict[str, dict]:
    """application -> inventory _source, filtered by the user's team scope in Python."""
    try:
        res = es().search(index=IDX["inventory"], size=10000, query={"match_all": {}})
    except HTTPException:
        raise
    except Exception as exc:
        raise IntegrationUnavailable("Elasticsearch", f"inventory read failed: {exc}")
    out: dict[str, dict] = {}
    for h in res.get("hits", {}).get("hits", []):
        src = h.get("_source") or {}
        app = src.get("application") or src.get("app")
        if not app:
            continue
        if user.can_see_row(_teams_of(src)):
            out[app] = src
    return out


def _env_versions(src: dict) -> dict[str, str]:
    """Best-effort env → deployed version from an inventory doc."""
    out: dict[str, str] = {}
    for env in ENVS:
        v = src.get(f"{env}_version")
        if not v and isinstance(src.get(env), dict):
            v = src[env].get("version")
        if v:
            out[env] = str(v)
    return out


# ------------------------------------------------------------------ ES aggregations
def _counts(src: dict) -> dict:
    return {s: int(src.get(SEV_FIELDS[s]) or 0) for s in SEVERITIES}


def _latest_per_app(scanner: str, application: str | None = None) -> dict[str, dict]:
    """application -> latest scan doc _source for a scanner index."""
    body = {
        "size": 0,
        "aggs": {"apps": {
            "terms": {"field": "application", "size": 5000},
            "aggs": {"latest": {"top_hits": {
                "size": 1,
                "sort": [{"enddate": {"order": "desc"}}],
                "_source": ["application", "codeversion", "enddate",
                            "Vcritical", "Vhigh", "Vmedium", "Vlow"],
            }}},
        }},
    }
    if application:
        body["query"] = {"term": {"application": application}}
    try:
        res = es().search(index=IDX[scanner], **body)
    except HTTPException:
        raise
    except Exception as exc:
        raise IntegrationUnavailable("Elasticsearch", f"{IDX[scanner]} aggregation failed: {exc}")
    out: dict[str, dict] = {}
    for b in res.get("aggregations", {}).get("apps", {}).get("buckets", []):
        hits = b["latest"]["hits"]["hits"]
        if hits:
            out[str(b["key"])] = hits[0]["_source"]
    return out


def _versions_of_app(scanner: str, application: str) -> dict[str, dict]:
    """codeversion -> latest scan doc _source for one application in a scanner index."""
    body = {
        "size": 0,
        "query": {"term": {"application": application}},
        "aggs": {"vers": {
            "terms": {"field": "codeversion", "size": 200},
            "aggs": {"latest": {"top_hits": {
                "size": 1,
                "sort": [{"enddate": {"order": "desc"}}],
                "_source": ["codeversion", "enddate", "Vcritical", "Vhigh", "Vmedium", "Vlow"],
            }}},
        }},
    }
    try:
        res = es().search(index=IDX[scanner], **body)
    except HTTPException:
        raise
    except Exception as exc:
        raise IntegrationUnavailable("Elasticsearch", f"{IDX[scanner]} aggregation failed: {exc}")
    out: dict[str, dict] = {}
    for b in res.get("aggregations", {}).get("vers", {}).get("buckets", []):
        hits = b["latest"]["hits"]["hits"]
        if hits:
            out[str(b["key"])] = hits[0]["_source"]
    return out


# --------------------------------------------------------------------- summary
def summary(user: User, scanner: str = "all", q: str = "", project: str = "",
            only_findings: bool = False, severity_floor: str = "low",
            page: int = 1, size: int = 50) -> dict:
    wanted = SCANNERS if scanner in ("", "all") else [scanner]
    if any(s not in SCANNERS for s in wanted):
        raise HTTPException(status_code=404, detail=f"Unknown scanner: {scanner!r}")
    kept = _kept_severities(severity_floor)
    ql = (q or "").strip().lower()

    inventory = _visible_inventory(user)
    per_scanner = {sc: _latest_per_app(sc) for sc in wanted}

    rows: list[dict] = []
    apps_total = 0
    apps_scanned = 0
    totals = {s: 0 for s in SEVERITIES}

    for app, src in sorted(inventory.items()):
        proj = str(src.get("project") or "")
        if project and proj != project:
            continue
        if ql and ql not in app.lower() and ql not in proj.lower():
            continue
        apps_total += 1
        cells: dict[str, dict] = {}
        best_ver, best_when = "", ""
        for sc in wanted:
            doc = per_scanner[sc].get(app)
            if not doc:
                continue
            counts = _counts(doc)
            when = str(doc.get("enddate") or "")
            cells[sc] = {**{s: (counts[s] if s in kept else 0) for s in SEVERITIES},
                         "when": when}
            if when >= best_when:
                best_ver, best_when = str(doc.get("codeversion") or ""), when
        if not cells:
            continue
        apps_scanned += 1
        row_tot = {s: sum(c[s] for c in cells.values()) for s in SEVERITIES}
        if only_findings and sum(row_tot.values()) == 0:
            continue
        for s in SEVERITIES:
            totals[s] += row_tot[s]
        env_of_version = ""
        for env in reversed(ENVS):  # prefer prd
            if _env_versions(src).get(env) == best_ver:
                env_of_version = env
                break
        rows.append({
            "application": app,
            "project": proj,
            "version": best_ver,
            "env_of_version": env_of_version,
            "scanners": cells,
            "total_critical": row_tot["critical"],
            "total_high": row_tot["high"],
        })

    rows.sort(key=lambda r: (-r["total_critical"], -r["total_high"], r["application"]))
    total = len(rows)
    size = max(1, min(int(size or 50), 200))
    pages = max(1, math.ceil(total / size))
    page = max(1, min(int(page or 1), pages))
    return {
        "rows": rows[(page - 1) * size: page * size],
        "totals": {**totals, "apps_scanned": apps_scanned, "apps_total": apps_total},
        "page": page,
        "pages": pages,
        "total": total,
    }


# ----------------------------------------------------------------- drill-down
def app_detail(user: User, project: str, application: str) -> dict:
    inventory = _visible_inventory(user)
    src = inventory.get(application)
    if src is None or (project and str(src.get("project") or "") != project):
        raise HTTPException(status_code=404, detail="Application not found in your scope")

    env_vers = _env_versions(src)
    prd_ver = env_vers.get("prd")
    per_scanner = {sc: _versions_of_app(sc, application) for sc in SCANNERS}

    all_versions: set[str] = set(env_vers.values())
    for docs in per_scanner.values():
        all_versions.update(docs.keys())

    def _ver_key(v: str):
        try:
            return tuple(int(x) for x in v.split("-")[0].split("."))
        except ValueError:
            return (0, 0, 0)

    out = []
    for v in sorted(all_versions, key=_ver_key, reverse=True):
        cells: dict[str, dict] = {}
        for sc in SCANNERS:
            doc = per_scanner[sc].get(v)
            if not doc:
                continue
            counts = _counts(doc)
            delta = None
            if prd_ver and prd_ver in per_scanner[sc]:
                p = _counts(per_scanner[sc][prd_ver])
                delta = {s: counts[s] - p[s] for s in SEVERITIES}
            cells[sc] = {**counts, "when": str(doc.get("enddate") or ""), "delta": delta}
        out.append({
            "version": v,
            "envs": [env for env, ev in env_vers.items() if ev == v],
            "scanners": cells,
        })
    return {"application": application, "project": project, "prd_version": prd_ver,
            "versions": out}


# --------------------------------------------------------------------- report
def report(user: User, scanner: str, project: str, application: str, version: str) -> str:
    if scanner not in SCANNERS:
        raise HTTPException(status_code=404, detail=f"Unknown scanner: {scanner!r}")
    inventory = _visible_inventory(user)
    src = inventory.get(application)
    if src is None or (project and str(src.get("project") or "") != project):
        raise HTTPException(status_code=404, detail="Application not found in your scope")

    s = get_settings()
    key = s.prisma_s3_key_pattern.format(project=project, application=application,
                                         version=version)
    try:
        obj = s3_client().get_object(Bucket=s.prisma_s3_bucket, Key=key)
        raw = obj["Body"].read()
    except HTTPException:
        raise
    except Exception as exc:
        raise IntegrationUnavailable("S3", f"get {s.prisma_s3_bucket}/{key} failed: {exc}")

    text = raw.decode("utf-8", errors="replace")
    if text.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
        return text  # already an HTML report — proxy as-is
    # plain-text scanner log → wrap in a minimal dark page
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html_mod.escape(application)} {html_mod.escape(version)}</title></head>"
        "<body style='margin:0;background:#0B1020;color:#E8ECFA;"
        "font-family:ui-monospace,Menlo,monospace;padding:24px'>"
        f"<pre style='white-space:pre-wrap;font-size:12px'>{html_mod.escape(text)}</pre>"
        "</body></html>"
    )
