"""ELK logging-health monitor.

Loops the inventory projects/apps and reports, per app, the state of its
application-log indices: how many, their total size and doc count, which
environments/logtypes/weeks they span, the newest logged document, and whether
the `@timestamp` field is a proper `date` everywhere (the #1 cause of a log
index that silently can't be time-filtered).

Index naming (parsed with the KNOWN project/app lists so hyphenated names are
unambiguous):
    ${log_index_prefix}-${project}-${env}-${app}-${logtype}-yyyy.ww

Two Elasticsearch connections:
  * PRIMARY (ES_URL / ES_API_KEY)      — serves prd-environment log indices
                                          (the same connection the KPI uses).
  * NON-PRD (ES_NONPRD_URL / …_KEY)    — serves every other environment.

Each connection is queried with just three cheap requests: `_cat/indices`
(sizes + docs), `_mapping/field/@timestamp` (per-index field type), and one
`_msearch` (max @timestamp per app). Demo mode fabricates a realistic estate."""

import datetime as dt
import json
import re
import time

import requests

from ..config import settings

_CACHE: dict = {"at": 0.0, "payload": None}
_TTL = 120

_WEEK_RE = re.compile(r"-(\d+)\.(\d{1,3})$")   # -yyyy.ww suffix (year width validated)


# ------------------------------------------------------------------ helpers
def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _hsize(n: int) -> str:
    f = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{int(f)} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def _ms_to_iso(v) -> str | None:
    """ES `max` on a date field returns epoch millis — to an ISO string."""
    if v is None:
        return None
    try:
        return dt.datetime.utcfromtimestamp(float(v) / 1000.0).replace(
            microsecond=0).isoformat() + "Z"
    except (ValueError, OverflowError, OSError):
        return None


def _age_hours(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None) if t.tzinfo else t
        return round((_now() - t).total_seconds() / 3600.0, 1)
    except ValueError:
        return None


def _match_token(low_rest: str, candidates: list[str]) -> tuple[str | None, int]:
    """Longest known candidate (case-insensitive) that prefixes `low_rest` as a
    whole `-`-separated token. Returns (canonical_name, chars_consumed)."""
    best = None
    for c in candidates:
        cl = c.lower()
        if low_rest == cl or low_rest.startswith(cl + "-"):
            if best is None or len(cl) > len(best[1]):
                best = (c, cl)
    return (best[0], len(best[1])) if best else (None, 0)


def _parse_index(name: str, known: dict) -> dict | None:
    """Split an index name into {prefix, project, env, app, logtype, week} using
    the known prefix + project/app/env lists (so hyphenated components stay
    unambiguous). Returns None when it doesn't resolve to a known project+app."""
    prefix = None
    for p in known["prefixes"]:                  # longest-first (vmlin/vmwin > oc)
        if name.startswith(p + "-"):
            prefix = p
            break
    if prefix is None:
        return None
    rest = name[len(prefix) + 1:]
    week = None
    bad_week = False
    m = _WEEK_RE.search(rest)
    if m:
        yr, wk = m.group(1), int(m.group(2))
        week = f"{yr}.{wk:02d}"
        # an illogical year (not 4 digits / out of range) or week (>53) is a real
        # naming bug — flag it instead of silently mis-parsing the suffix
        bad_week = not (len(yr) == 4 and 2000 <= int(yr) <= 2100 and 1 <= wk <= 53)
        rest = rest[:m.start()]
    # rest == project-env-app-logtype (any part may contain '-')
    proj, n = _match_token(rest.lower(), known["projects"])
    if proj is None:
        return None
    rest = rest[n:].lstrip("-")
    env, n = _match_token(rest.lower(), known["envs"])
    if env is None:                              # unknown env token → first seg
        env = rest.split("-", 1)[0]
        rest = rest[len(env):].lstrip("-")
    else:
        rest = rest[n:].lstrip("-")
    apps = known["apps_by_project"].get(proj) or known["apps"]
    app, n = _match_token(rest.lower(), apps)
    if app is None:
        return None
    logtype = rest[n:].lstrip("-") or "—"
    return {"prefix": prefix, "project": proj, "env": env.lower(), "app": app,
            "logtype": logtype, "week": week, "bad_week": bad_week}


# ------------------------------------------------------------------ ES calls
def _headers(key: str) -> dict:
    return {"Authorization": f"ApiKey {key}"}


def _cat_indices(conn: dict, pattern: str) -> list[dict]:
    r = requests.get(f"{conn['url']}/_cat/indices/{pattern}",
                     params={"format": "json", "bytes": "b",
                             "h": "index,health,status,docs.count,store.size", "s": "index"},
                     headers=_headers(conn["key"]), timeout=30, verify=conn["verify"])
    if r.status_code == 404:                     # no indices match the pattern
        return []
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, list) else []


def _ts_field_types(conn: dict, pattern: str) -> dict:
    """Per-index @timestamp mapping type ('date' / 'text' / … / None=unmapped)."""
    r = requests.get(f"{conn['url']}/{pattern}/_mapping/field/@timestamp",
                     headers=_headers(conn["key"]), timeout=30, verify=conn["verify"])
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    out: dict = {}
    for idx, m in (r.json() or {}).items():
        node = ((((m.get("mappings") or {}).get("@timestamp") or {}).get("mapping")
                 or {}).get("@timestamp") or {})
        out[idx] = node.get("type")
    return out


def _msearch_max_ts(conn: dict, groups: list[tuple]) -> dict:
    """One round trip: max(@timestamp) for each (key, [indices]) group."""
    if not groups:
        return {}
    lines = []
    for _key, idxs in groups:
        lines.append(json.dumps({"index": ",".join(idxs)}))
        lines.append(json.dumps({"size": 0, "track_total_hits": False,
                                 "aggs": {"m": {"max": {"field": "@timestamp"}}}}))
    payload = "\n".join(lines) + "\n"
    r = requests.post(f"{conn['url']}/_msearch", data=payload,
                      headers={**_headers(conn["key"]),
                               "Content-Type": "application/x-ndjson"},
                      timeout=60, verify=conn["verify"])
    r.raise_for_status()
    resp = r.json().get("responses", [])
    out = {}
    for (key, _idxs), res in zip(groups, resp):
        out[key] = _ms_to_iso((((res or {}).get("aggregations") or {}).get("m") or {}).get("value"))
    return out


def _connections() -> list[dict]:
    return [
        {"kind": "prd", "label": "prd", "url": settings.es_url.rstrip("/"),
         "key": settings.es_api_key, "verify": settings.es_verify_ssl,
         "expect_envs": set(settings.log_prd_env_list)},
        {"kind": "nonprd", "label": "non-prd", "url": settings.es_nonprd_url.rstrip("/"),
         "key": settings.es_nonprd_api_key, "verify": settings.es_nonprd_verify_ssl,
         "expect_envs": None},   # everything not served by prd
    ]


# ------------------------------------------------------------------ assemble
def _blank_app(project: str, app: str) -> dict:
    return {"project": project, "app": app, "index_list": []}


# per-env health score deductions from 100 (higher = healthier)
_PENALTY = {"stale": 50, "timestamp": 40, "bad_week": 20}
# the issue keys used across scoring + the issue-type filter
ISSUE_KEYS = ("no_logs", "stale", "timestamp", "bad_week", "clash", "unsupported")


def _env_score(no_logs: bool, stale: bool, ts_bad: bool, bad_week: bool) -> int:
    if no_logs:
        return 0
    s = 100 - (_PENALTY["stale"] if stale else 0) \
        - (_PENALTY["timestamp"] if ts_bad else 0) \
        - (_PENALTY["bad_week"] if bad_week else 0)
    return max(s, 0)


def _finalize_app(rec: dict, stale_h: int, expected_envs, last_map: dict,
                  meta: dict | None) -> dict:
    idxs = rec.pop("index_list")
    pname, app = rec["project"], rec["app"]
    meta = meta or {}
    status = meta.get("status")
    if status is None and idxs:          # found in ES but not in inventory
        status = "supported"
    monitored = status in ("supported", "fallback")

    def _ts_bad(indices):
        types: dict = {}
        for i in indices:
            types.setdefault(i.get("ts_type") or "unmapped", []).append(i["index"])
        return {t: v for t, v in types.items() if t != "date"}, types

    by_env: dict = {}
    for i in idxs:
        by_env.setdefault(i.get("env") or "?", []).append(i)

    # env_stats covers every EXPECTED env (from inventory) ∪ envs seen in indices,
    # so an env that simply isn't logging shows up as a "no logs" issue there
    env_stats = []
    for env in sorted(set(expected_envs or []) | set(by_env)):
        eidx = by_env.get(env, [])
        bad, _types = _ts_bad(eidx)
        lasts = [x for x in last_map.get((pname, app, env), []) if x]
        last = max(lasts) if lasts else None
        age = _age_hours(last)
        bad_week = sorted({i["index"] for i in eidx if i.get("bad_week")})
        no_logs = len(eidx) == 0
        stale = (not no_logs) and (age is None or age > stale_h)
        ts_ok = (not no_logs) and not bad
        issues = []
        if not monitored:
            score = None
        elif no_logs:
            issues, score = ["no_logs"], 0
        else:
            if stale:
                issues.append("stale")
            if not ts_ok:
                issues.append("timestamp")
            if bad_week:
                issues.append("bad_week")
            score = _env_score(no_logs, stale, not ts_ok, bool(bad_week))
        env_stats.append({
            "env": env, "indices": len(eidx),
            "size_bytes": sum(i["size_bytes"] for i in eidx),
            "size_h": _hsize(sum(i["size_bytes"] for i in eidx)),
            "docs": sum(i["docs"] for i in eidx),
            "logtypes": sorted({i["logtype"] for i in eidx if i.get("logtype")}),
            "sources": sorted({i["source"] for i in eidx}),
            "last_logged": last, "last_logged_age_h": age,
            "no_logs": no_logs, "stale": stale, "ts_ok": ts_ok,
            "ts_bad_indices": sorted({x for v in bad.values() for x in v})[:10],
            "bad_week_indices": bad_week[:10],
            "issues": issues, "score": score})

    # app-level aggregates
    bad, types = _ts_bad(idxs)
    weeks = sorted({i["week"] for i in idxs if i.get("week") and not i.get("bad_week")})
    all_last = [s["last_logged"] for s in env_stats if s["last_logged"]]
    rec.update({
        "indices": len(idxs),
        "size_bytes": sum(i["size_bytes"] for i in idxs),
        "size_h": _hsize(sum(i["size_bytes"] for i in idxs)),
        "docs": sum(i["docs"] for i in idxs),
        "envs": sorted(by_env),
        "expected_envs": sorted(set(expected_envs or [])),
        "logtypes": sorted({i["logtype"] for i in idxs if i.get("logtype")}),
        "weeks": len(weeks), "week_span": [weeks[0], weeks[-1]] if weeks else None,
        "latest_week": weeks[-1] if weeks else None,
        "sources": sorted({i["source"] for i in idxs}),
        "last_logged": max(all_last) if all_last else None,
        "ts_types": {t: len(v) for t, v in types.items()},
        "ts_bad_indices": sorted({x for v in bad.values() for x in v})[:25],
        "bad_week_indices": sorted({i["index"] for i in idxs if i.get("bad_week")})[:25],
        "monitored": monitored, "platform_status": status,
        "env_stats": env_stats,
        # meta / platform fields (were _apply_app_meta)
        "prefix": meta.get("prefix"), "deploy_platform": meta.get("deploy_platform"),
        "prefix_source": meta.get("source"),
        "app_platform": meta.get("app_platform"),
        "project_platform": meta.get("project_platform"),
        "discrepancy": meta.get("discrepancy", False),
    })
    rec["last_logged_age_h"] = _age_hours(rec["last_logged"])
    rec["no_logs"] = monitored and len(idxs) == 0
    rec["ts_ok"] = len(idxs) > 0 and not bad
    rec["envs_stale"] = sum(1 for s in env_stats if s["stale"])
    rec["envs_no_logs"] = sum(1 for s in env_stats if monitored and s["no_logs"])
    rec["stale"] = rec["envs_stale"] > 0

    # per-app issue set (drives the issue-type filter) + health score
    issues = set()
    if not monitored and status == "unsupported":
        issues.add("unsupported")
    if monitored and (len(idxs) == 0 or rec["envs_no_logs"]):
        issues.add("no_logs")
    if rec["envs_stale"]:
        issues.add("stale")
    if len(idxs) > 0 and bad:
        issues.add("timestamp")
    if rec["bad_week_indices"]:
        issues.add("bad_week")
    if rec["discrepancy"]:
        issues.add("clash")
    rec["issues"] = sorted(issues)
    if not monitored:
        rec["score"] = None
    else:
        env_scores = [s["score"] for s in env_stats if s["score"] is not None]
        base = (sum(env_scores) / len(env_scores)) if env_scores else 0
        if rec["discrepancy"]:
            base -= 10                    # config-hygiene deduction
        rec["score"] = max(int(round(base)), 0)

    rec["index_list"] = sorted(
        idxs, key=lambda x: (x.get("week") or "", x["index"]), reverse=True)[:60]
    return rec


def _assemble(records: list[dict], last_map: dict, conn_status: dict,
              projects: list[dict], known: dict, source: str,
              app_meta: dict, proj_meta: dict, note: str = "",
              diag: dict | None = None) -> dict:
    """Shape raw per-index records (+ per-app last-logged map) into the payload.
    `records` matched rows carry project/app/env/logtype/week; unmatched rows
    (unknown project/app) are collected separately. `app_meta` gives each
    (project, app) its effective deploy_platform + prefix + clash flag;
    `proj_meta` the project-global platform. Shared by live and demo."""
    stale_h = settings.log_stale_hours
    apps_model: dict = {}
    unmatched: list = []
    for r in records:
        if r.get("app") and r.get("project"):
            key = (r["project"], r["app"])
            apps_model.setdefault(key, _blank_app(*key))["index_list"].append(r)
        else:
            unmatched.append({k: r[k] for k in ("index", "source", "size_bytes",
                                                "docs", "health", "ts_type") if k in r})
    all_envs: set = set()
    all_logtypes: set = set()

    def _mean(xs):
        xs = [x for x in xs if x is not None]
        return int(round(sum(xs) / len(xs))) if xs else None

    def _proj_totals(rows):
        return {
            "apps": len(rows),
            "indices": sum(a["indices"] for a in rows),
            "size_bytes": sum(a["size_bytes"] for a in rows),
            "size_h": _hsize(sum(a["size_bytes"] for a in rows)),
            "docs": sum(a["docs"] for a in rows),
            "no_logs": sum(1 for a in rows if "no_logs" in a["issues"]),
            "ts_bad": sum(1 for a in rows if "timestamp" in a["issues"]),
            "stale": sum(1 for a in rows if "stale" in a["issues"]),
            "bad_week": sum(1 for a in rows if "bad_week" in a["issues"]),
            "discrepancies": sum(1 for a in rows if "clash" in a["issues"]),
            "unsupported": sum(1 for a in rows if "unsupported" in a["issues"]),
        }

    def _fin(rec, pname, expected_envs, not_in_inv=False):
        a = _finalize_app(rec, stale_h, expected_envs, last_map,
                          app_meta.get((pname, rec["app"])))
        if not_in_inv:
            a["not_in_inventory"] = True
        all_envs.update(a["envs"])
        all_logtypes.update(a["logtypes"])
        return a

    # score-first sort: worst (lowest score) leads; no-logs/unsupported after data
    def _rank(a):
        return (a["score"] is None, a["score"] if a["score"] is not None else 999,
                -a["size_bytes"])

    projects_out = []
    for p in projects:
        pname = p["name"]
        meta = proj_meta.get(pname, {})
        rows = [_fin(apps_model.pop((pname, app), None) or _blank_app(pname, app),
                     pname, p.get("envs", [])) for app in p.get("apps", [])]
        for (qp, qa) in [k for k in list(apps_model) if k[0] == pname]:
            rows.append(_fin(apps_model.pop((qp, qa)), pname, [], not_in_inv=True))
        rows.sort(key=_rank)
        projects_out.append({
            "name": pname, "deploy_platform": meta.get("deploy_platform"),
            "prefix": meta.get("prefix"),
            "no_prefix": bool(rows) and not any(a.get("prefix") for a in rows),
            "score": _mean([a["score"] for a in rows]),
            "apps": rows, "totals": _proj_totals(rows)})

    for pname in sorted({k[0] for k in apps_model}):
        rows = [_fin(apps_model.pop(k), pname, [], not_in_inv=True)
                for k in [k for k in list(apps_model) if k[0] == pname]]
        rows.sort(key=_rank)
        projects_out.append({"name": pname, "not_in_inventory": True,
                             "score": _mean([a["score"] for a in rows]),
                             "apps": rows, "totals": _proj_totals(rows)})

    unmatched.sort(key=lambda u: -(u.get("size_bytes") or 0))
    legend = sorted({(m.get("deploy_platform"), m.get("prefix"))
                     for m in app_meta.values() if m.get("deploy_platform") and m.get("prefix")})
    all_apps = [a for po in projects_out for a in po["apps"]]

    def cnt(key):
        return sum(1 for a in all_apps if key in a["issues"])
    summary = {
        "projects": len(projects_out), "apps": len(all_apps),
        "indices": sum(a["indices"] for a in all_apps),
        "size_bytes": sum(a["size_bytes"] for a in all_apps),
        "size_h": _hsize(sum(a["size_bytes"] for a in all_apps)),
        "docs": sum(a["docs"] for a in all_apps),
        "envs": sorted(all_envs), "logtypes": sorted(all_logtypes),
        "unmatched": len(unmatched),
        "overall_score": _mean([a["score"] for a in all_apps]),
        "apps_no_logs": cnt("no_logs"), "apps_stale": cnt("stale"),
        "apps_ts_bad": cnt("timestamp"), "apps_bad_week": cnt("bad_week"),
        "discrepancies": cnt("clash"), "apps_unsupported": cnt("unsupported"),
        "apps_no_platform": sum(1 for a in all_apps if not a.get("prefix")),
        "projects_no_platform": sum(1 for po in projects_out if po.get("no_prefix")),
        "envs_stale": sum(a.get("envs_stale", 0) for a in all_apps),
        "envs_no_logs": sum(a.get("envs_no_logs", 0) for a in all_apps),
    }
    return {"source": source, "note": note, "stale_hours": stale_h,
            "prefixes": sorted({m["prefix"] for m in app_meta.values() if m.get("prefix")}),
            "platform_legend": [{"platform": pl, "prefix": pr} for pl, pr in legend],
            "connections": conn_status, "summary": summary,
            "projects": projects_out, "unmatched": unmatched[:50],
            "diagnostics": diag}


def _known(projects: list[dict], prefixes) -> dict:
    return {
        "projects": [p["name"] for p in projects],
        "apps_by_project": {p["name"]: list(p.get("apps", [])) for p in projects},
        "apps": sorted({a for p in projects for a in p.get("apps", [])}),
        "envs": sorted({e for p in projects for e in p.get("envs", [])})
                or ["dev", "qc", "uat", "prd"],
        "prefixes": sorted(set(prefixes), key=len, reverse=True),
    }


def _clean(v) -> str | None:
    s = ("" if v is None else str(v)).strip()
    return s or None


def _platform_prefix(platform, pmap: dict) -> str | None:
    p = _clean(platform)
    return pmap.get(p.lower()) if p else None


def _app_meta(projects: list[dict]) -> tuple[dict, dict]:
    """Resolve the log index prefix PER (project, app) from `deploy_platform`.

    deploy_platform is read app-first: the app's own group_vars
    (group_vars/<app>/*.yml incl. cicd.yml → `config.app_vars[app]`), falling
    back to the project-wide group_vars/all (`config.project_vars`). The map is
    OCP→oc · LinuxVM→vmlin · WindowsVM→vmwin · K8s→k8s (+ LOG_PLATFORM_PREFIXES),
    then LOG_INDEX_PREFIX as a last-resort fallback. Returns (app_meta, proj_meta):
      app_meta[(project, app)] = {deploy_platform (effective), prefix, source
        ('app'|'project'|'fallback'|None), app_platform, project_platform, discrepancy}
      proj_meta[project] = {deploy_platform (project-global), prefix}."""
    pmap = settings.log_platform_prefix_map
    fallback = (settings.log_index_prefix or "").strip() or None
    app_meta: dict = {}
    proj_meta: dict = {}
    for p in projects:
        cfg = p.get("config") or {}
        pvars = cfg.get("project_vars") or {}
        avars = cfg.get("app_vars") or {}
        proj_plat = _clean(pvars.get("deploy_platform") or p.get("deploy_platform"))
        proj_meta[p["name"]] = {"deploy_platform": proj_plat,
                                "prefix": _platform_prefix(proj_plat, pmap)}
        for app in p.get("apps", []):
            app_plat = _clean((avars.get(app) or {}).get("deploy_platform"))
            eff_plat = app_plat or proj_plat            # app group_vars win (Ansible)
            src = "app" if app_plat else ("project" if proj_plat else None)
            known = _platform_prefix(eff_plat, pmap)
            if eff_plat and known:                      # a platform we monitor
                prefix, status = known, "supported"
            elif eff_plat:                              # a platform, but not one we map
                prefix, status = None, "unsupported"
            elif fallback:                              # no platform anywhere → fallback
                prefix, status, src = fallback, "fallback", "fallback"
            else:
                prefix, status = None, "none"
            app_meta[(p["name"], app)] = {
                "deploy_platform": eff_plat, "prefix": prefix, "source": src,
                "status": status, "app_platform": app_plat, "project_platform": proj_plat,
                "discrepancy": bool(app_plat and proj_plat
                                    and app_plat.lower() != proj_plat.lower())}
    return app_meta, proj_meta


# the user-facing knob names (host/compose env is QO_-prefixed; the container
# env pydantic reads is the bare name — surface the QO_ names users actually set)
_CONN_KNOBS = {"prd": "QO_ES_URL / QO_ES_API_KEY",
               "nonprd": "QO_ES_NONPRD_URL / QO_ES_NONPRD_API_KEY"}


def _ping(conn: dict) -> tuple[bool, str | None]:
    """Cheap reachability probe (used when there's no prefix to query yet):
    is the ES host up and the ApiKey accepted? (reachable, error)."""
    try:
        r = requests.get(f"{conn['url']}/_cluster/health",
                         headers=_headers(conn["key"]), timeout=10, verify=conn["verify"])
    except requests.RequestException as exc:
        return False, str(exc)[:200]
    if r.status_code in (401, 403):
        return False, f"authentication rejected (HTTP {r.status_code}) — check the ApiKey"
    if not r.ok:
        return False, _es_reason(r) or f"HTTP {r.status_code}"
    return True, None


def _inventory_diag(inv: dict, projects: list[dict], app_meta: dict,
                    proj_meta: dict, prefixes: list) -> dict:
    """What the page managed to parse out of the inventories repo — so a blank
    page is debuggable (mirrors the Repositories inventory panel). Includes the
    per-project global deploy_platform, per-app overrides, project↔app clashes,
    and any apps whose prefix couldn't be resolved."""
    proj_rows = []
    for p in projects:
        pm = proj_meta.get(p["name"]) or {}
        overrides, unresolved = [], []
        for app in p.get("apps", []):
            am = app_meta.get((p["name"], app)) or {}
            if am.get("app_platform"):
                overrides.append({"app": app, "platform": am["app_platform"],
                                  "prefix": am.get("prefix"),
                                  "discrepancy": am.get("discrepancy", False)})
            if not am.get("prefix"):
                unresolved.append(app)
        proj_rows.append({
            "project": p["name"],
            "deploy_platform": pm.get("deploy_platform"),
            "prefix": pm.get("prefix"),
            "apps": len(p.get("apps", [])), "envs": p.get("envs", []),
            "app_overrides": overrides, "unresolved_apps": unresolved})
    return {
        "inventory_source": inv.get("source"),
        "inventory_note": inv.get("note"),
        "projects": len(projects),
        "apps": sorted({a for p in projects for a in p.get("apps", [])}),
        "envs": sorted({e for p in projects for e in p.get("envs", [])}),
        "prefixes": list(prefixes),
        "platform_map": settings.log_platform_prefix_map,
        "project_platforms": proj_rows,
    }


# ------------------------------------------------------------------ live
def _live() -> dict:
    from . import inventory
    inv = inventory.parse()
    projects = inv.get("projects", [])
    app_meta, proj_meta = _app_meta(projects) if projects else ({}, {})
    prefixes = sorted({m["prefix"] for m in app_meta.values() if m["prefix"]})
    diag = _inventory_diag(inv, projects, app_meta, proj_meta, prefixes)
    known = _known(projects, prefixes)
    pattern = ",".join(f"{p}-*" for p in prefixes)   # "" when nothing resolved yet

    # ALWAYS probe both connections and report their real status — even when no
    # prefix resolves — so a configured ES never wrongly shows "off".
    conn_status: dict = {}
    records: list = []
    last_map: dict = {}
    for conn in _connections():
        kind = conn["kind"]
        if not conn["url"] or not conn["key"]:
            missing = ("QO_ES_URL" if not conn["url"] else "QO_ES_API_KEY") if kind == "prd" \
                else ("QO_ES_NONPRD_URL" if not conn["url"] else "QO_ES_NONPRD_API_KEY")
            conn_status[kind] = {"label": conn["label"], "configured": False,
                                 "reachable": False, "indices": 0,
                                 "note": f"not configured — set {_CONN_KNOBS[kind]}"
                                         f" ({missing} is empty)"}
            continue
        if not pattern:                        # configured but no prefix to query yet
            ok, err = _ping(conn)
            conn_status[kind] = {"label": conn["label"], "configured": True,
                                 "reachable": ok, "indices": 0, "url": conn["url"],
                                 "error": err}
            continue
        try:
            cats = _cat_indices(conn, pattern)
            ts_types = _ts_field_types(conn, pattern)
        except requests.RequestException as exc:
            conn_status[kind] = {"label": conn["label"], "configured": True,
                                 "reachable": False, "indices": 0, "url": conn["url"],
                                 "error": str(exc)[:200]}
            continue
        conn_groups: dict = {}
        unexpected: set = set()
        for row in cats:
            name = row.get("index", "")
            if not name:
                continue
            p = _parse_index(name, known)
            rec = {"index": name, "source": kind,
                   "size_bytes": int(row.get("store.size") or 0),
                   "docs": int(row.get("docs.count") or 0),
                   "health": row.get("health"), "ts_type": ts_types.get(name)}
            if p:
                rec.update(p)
                if conn["expect_envs"] is not None and p["env"] not in conn["expect_envs"]:
                    unexpected.add(p["env"])
                elif conn["expect_envs"] is None and p["env"] in set(settings.log_prd_env_list):
                    unexpected.add(p["env"])
                conn_groups.setdefault((p["project"], p["app"], p["env"]), []).append(name)
            records.append(rec)
        # newest @timestamp per (app, ENV) on this connection (one round trip) —
        # staleness must be judged per environment, not for the whole app
        try:
            got = _msearch_max_ts(conn, [("\x00".join(k), v)
                                         for k, v in conn_groups.items()])
        except requests.RequestException:
            got = {}
        for k in conn_groups:
            iso = got.get("\x00".join(k))
            if iso:
                last_map.setdefault(k, []).append(iso)
        conn_status[kind] = {"label": conn["label"], "configured": True,
                             "reachable": True, "indices": len(cats), "url": conn["url"]}
        if unexpected:
            conn_status[kind]["unexpected_envs"] = sorted(unexpected)

    note = ""
    if not projects:
        note = (inv.get("note") or "the 'inventories' repo isn't cloned/parsed yet — "
                "add & clone it on the Repositories page, then re-analyze.")
    elif not prefixes:
        note = ("no log index prefix resolved: no app or project declares a known "
                "`deploy_platform` (OCP / LinuxVM / WindowsVM / K8s) and "
                "QO_LOG_INDEX_PREFIX has no fallback. See the per-project/app detection below.")
    return _assemble(records, last_map, conn_status, projects, known, "live",
                     app_meta, proj_meta, note, diag)


# ------------------------------------------------------------------ demo
def _iso_week(d: dt.datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}.{w:02d}"


def _demo() -> dict:
    from . import inventory
    inv = inventory.parse()
    projects = inv.get("projects", [])
    app_meta, proj_meta = _app_meta(projects)
    prefixes = sorted({m["prefix"] for m in app_meta.values() if m["prefix"]})
    known = _known(projects, prefixes)
    diag = _inventory_diag(inv, projects, app_meta, proj_meta, prefixes)
    now = _now()
    weeks = [_iso_week(now - dt.timedelta(weeks=i)) for i in range(4)][::-1]  # oldest→newest
    logtypes = ["application", "access", "error"]
    records: list = []
    last_map: dict = {}
    # scripted quirks for a good story
    NO_LOGS = {("Control", "team-configs")}          # app with no indices at all
    TS_BAD = {("Platform", "checkout")}              # @timestamp = text in dev
    STALE_ENV = {("Platform", "notifications", "prd")}  # stale in PRD only
    MISSING_ENV = {("Platform", "payments", "qc")}   # payments not logging in qc
    BAD_WEEK = {("Platform", "checkout", "prd")}     # an illogical 5-digit year index

    def env_conn(env):  # prd env served by prd connection, else non-prd
        return "prd" if env in set(settings.log_prd_env_list) else "nonprd"

    for p in projects:
        pname = p["name"]
        envs = p.get("envs", []) or ["dev", "prd"]
        for app in p.get("apps", []):
            key = (pname, app)
            prefix = (app_meta.get(key) or {}).get("prefix")   # per-app prefix
            if key in NO_LOGS or not prefix:      # unsupported/none platform → not checked
                continue
            for env in envs:
                if (pname, app, env) in MISSING_ENV:   # this env simply isn't logging
                    continue
                src = env_conn(env)
                for wk in weeks:
                    for lt in logtypes:
                        if lt == "error" and (hash((app, env, wk)) % 3):
                            continue
                        size = 40_000_000 + (hash((app, env, wk, lt)) % 900) * 1_000_000
                        docs = 50_000 + (hash((lt, app, wk)) % 800) * 1000
                        idx = f"{prefix}-{pname}-{env}-{app}-{lt}-{wk}"
                        ts_type = "text" if (key in TS_BAD and env == "dev"
                                             and lt == "application") else "date"
                        records.append({"index": idx, "source": src,
                                        "size_bytes": size, "docs": docs,
                                        "health": "green", "ts_type": ts_type,
                                        "prefix": prefix, "project": pname, "env": env,
                                        "app": app, "logtype": lt, "week": wk,
                                        "bad_week": False})
                # an index with an illogical 5-digit year → bad_week issue
                if (pname, app, env) in BAD_WEEK:
                    records.append({"index": f"{prefix}-{pname}-{env}-{app}-application-2{weeks[-1]}",
                                    "source": src, "size_bytes": 5_000_000, "docs": 3000,
                                    "health": "yellow", "ts_type": "date", "prefix": prefix,
                                    "project": pname, "env": env, "app": app,
                                    "logtype": "application", "week": f"2{weeks[-1]}",
                                    "bad_week": True})
                # last-logged PER ENV: fresh, except the scripted stale env
                if (pname, app, env) in STALE_ENV:
                    iso = (now - dt.timedelta(days=6, hours=2)).replace(microsecond=0).isoformat() + "Z"
                else:
                    iso = (now - dt.timedelta(minutes=6 + hash((app, env)) % 40)).replace(microsecond=0).isoformat() + "Z"
                last_map.setdefault((pname, app, env), []).append(iso)
    # a couple of stray indices that carry a valid prefix but don't map to any
    # known app/project (naming drift → surfaced in the "unmatched" section)
    for name in (f"oc-Platform-prd-legacy-batch-application-{weeks[-1]}",
                 f"vmwin-Sandbox-dev-scratch-debug-{weeks[-1]}"):
        records.append({"index": name, "source": "prd" if "-prd-" in name else "nonprd",
                        "size_bytes": 12_000_000, "docs": 8000, "health": "yellow",
                        "ts_type": "date"})
    conn_status = {
        "prd": {"label": "prd", "configured": True, "reachable": True,
                "indices": sum(1 for r in records if r["source"] == "prd"),
                "url": "https://es-prd.demo:9200"},
        "nonprd": {"label": "non-prd", "configured": True, "reachable": True,
                   "indices": sum(1 for r in records if r["source"] == "nonprd"),
                   "url": "https://es-nonprd.demo:9200"},
    }
    return _assemble(records, last_map, conn_status, projects, known, "demo",
                     app_meta, proj_meta, diag=diag)


# ------------------------------------------------ @timestamp sample inspector
def _looks_date(v) -> bool:
    from .elastic import _parse_es_date
    w = _parse_es_date(v)
    return bool(w and w.year >= 2000)


def _conn_by_kind(kind: str) -> dict | None:
    for c in _connections():
        if c["kind"] == kind:
            return c
    return None


def _sample_ts(conn: dict, index: str, size: int) -> dict:
    """A few docs' @timestamp from one index, each tagged whether the value
    actually parses as a date — so a text-mapped index's offending values are
    visible next to a healthy index's proper dates."""
    if not conn or not conn["url"] or not conn["key"]:
        return {"index": index, "error": "connection not configured", "docs": []}
    ts_type = _ts_field_types(conn, index).get(index)
    try:
        data = _es_search(index, {"size": size, "_source": ["@timestamp"],
                                  "query": {"exists": {"field": "@timestamp"}}})
    except (requests.RequestException, ValueError) as exc:
        return {"index": index, "ts_type": ts_type, "error": str(exc)[:200], "docs": []}
    docs = []
    for h in (data.get("hits", {}).get("hits", []) or []):
        val = (h.get("_source") or {}).get("@timestamp")
        docs.append({"id": h.get("_id"), "value": val, "is_date": _looks_date(val)})
    return {"index": index, "ts_type": ts_type, "docs": docs,
            "non_date": sum(1 for d in docs if not d["is_date"]), "sampled": len(docs)}


def ts_samples(index: str, source: str = "prd", good: str = "",
               good_source: str = "", size: int = 8) -> dict:
    """Sample @timestamp values from a suspect index (and, for contrast, a
    known-good sibling) to pinpoint what makes @timestamp not a proper date."""
    if not (index or "").strip():
        raise ValueError("index is required")
    if settings.demo_mode:
        return _demo_ts_samples(index, good)
    out = {"index": _sample_ts(_conn_by_kind(source), index.strip(), size)}
    if good.strip():
        out["good"] = _sample_ts(_conn_by_kind(good_source or source), good.strip(), size)
    return out


def _demo_ts_samples(index: str, good: str) -> dict:
    now = _now()
    def good_docs(n):
        return [{"id": f"doc-{i}", "value": (now - dt.timedelta(minutes=i * 7))
                 .replace(microsecond=0).isoformat() + "Z", "is_date": True} for i in range(n)]
    # the bad (text-mapped) index: a mix of valid strings and junk values
    bad_vals = ["2026-08-05T10:12:03Z", "N/A", "2026-08-05 10:11", "-",
                "pending", "2026-08-05T09:59:59Z", "null", "0000-00-00"]
    bad = [{"id": f"doc-{i}", "value": v, "is_date": _looks_date(v)}
           for i, v in enumerate(bad_vals)]
    res = {"index": {"index": index, "ts_type": "text", "docs": bad,
                     "non_date": sum(1 for d in bad if not d["is_date"]), "sampled": len(bad)}}
    if good:
        res["good"] = {"index": good, "ts_type": "date", "docs": good_docs(6),
                       "non_date": 0, "sampled": 6}
    return res


# ------------------------------------------------------------------ public
def analyze(force: bool = False) -> dict:
    if not force and _CACHE["payload"] and time.time() - _CACHE["at"] < _TTL:
        return {**_CACHE["payload"], "cached": True}
    payload = _demo() if settings.demo_mode else _live()
    _CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}


def invalidate() -> None:
    _CACHE.update(at=0.0, payload=None)
