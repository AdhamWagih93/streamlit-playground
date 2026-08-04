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

_WEEK_RE = re.compile(r"-(\d{4})\.(\d{1,2})$")   # -yyyy.ww suffix


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
    m = _WEEK_RE.search(rest)
    if m:
        week = f"{m.group(1)}.{int(m.group(2)):02d}"
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
            "logtype": logtype, "week": week}


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
    return {"project": project, "app": app, "index_list": [], "_last": []}


def _finalize_app(rec: dict, stale_h: int) -> dict:
    idxs = rec.pop("index_list")
    lasts = [x for x in rec.pop("_last", []) if x]
    rec["indices"] = len(idxs)
    rec["size_bytes"] = sum(i["size_bytes"] for i in idxs)
    rec["size_h"] = _hsize(rec["size_bytes"])
    rec["docs"] = sum(i["docs"] for i in idxs)
    rec["envs"] = sorted({i["env"] for i in idxs if i.get("env")})
    rec["logtypes"] = sorted({i["logtype"] for i in idxs if i.get("logtype")})
    weeks = sorted({i["week"] for i in idxs if i.get("week")})
    rec["weeks"] = len(weeks)
    rec["week_span"] = [weeks[0], weeks[-1]] if weeks else None
    rec["latest_week"] = weeks[-1] if weeks else None
    rec["sources"] = sorted({i["source"] for i in idxs})
    rec["last_logged"] = max(lasts) if lasts else None
    rec["last_logged_age_h"] = _age_hours(rec["last_logged"])
    # @timestamp health: is it a `date` in every one of this app's indices?
    types: dict = {}
    for i in idxs:
        types.setdefault(i.get("ts_type") or "unmapped", []).append(i["index"])
    bad = {t: v for t, v in types.items() if t != "date"}
    rec["ts_types"] = {t: len(v) for t, v in types.items()}
    rec["ts_bad_indices"] = sorted({x for v in bad.values() for x in v})[:25]
    rec["no_logs"] = len(idxs) == 0
    rec["ts_ok"] = len(idxs) > 0 and not bad
    rec["stale"] = (not rec["no_logs"] and (rec["last_logged_age_h"] is None
                                            or rec["last_logged_age_h"] > stale_h))
    rec["index_list"] = sorted(
        idxs, key=lambda x: (x.get("week") or "", x["index"]), reverse=True)[:60]
    return rec


def _assemble(records: list[dict], last_map: dict, conn_status: dict,
              projects: list[dict], known: dict, source: str,
              proj_meta: dict, note: str = "") -> dict:
    """Shape raw per-index records (+ per-app last-logged map) into the payload.
    `records` matched rows carry project/app/env/logtype/week; unmatched rows
    (unknown project/app) are collected separately. `proj_meta` gives each
    project its deploy_platform + resolved prefix. Shared by live and demo."""
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
    for key, lasts in last_map.items():
        if key in apps_model:
            apps_model[key]["_last"].extend(lasts if isinstance(lasts, list) else [lasts])

    # every inventory app gets a row, even with zero indices (→ no_logs)
    projects_out = []
    tot = {"indices": 0, "size_bytes": 0, "docs": 0}
    n_no_logs = n_stale = n_ts_bad = n_apps = 0
    all_envs: set = set()
    all_logtypes: set = set()
    n_no_platform = 0
    for p in projects:
        pname = p["name"]
        meta = proj_meta.get(pname, {})
        rows = []
        for app in p.get("apps", []):
            rec = apps_model.pop((pname, app), None) or _blank_app(pname, app)
            rows.append(_finalize_app(rec, stale_h))
        # apps that exist in indices under this project but not in the inventory
        for (qp, qa) in [k for k in list(apps_model) if k[0] == pname]:
            rows.append(_finalize_app(apps_model.pop((qp, qa)), stale_h))
            rows[-1]["not_in_inventory"] = True
        for a in rows:
            a["prefix"] = meta.get("prefix")
            a["deploy_platform"] = meta.get("deploy_platform")
            n_apps += 1
            tot["indices"] += a["indices"]
            tot["size_bytes"] += a["size_bytes"]
            tot["docs"] += a["docs"]
            all_envs.update(a["envs"])
            all_logtypes.update(a["logtypes"])
            n_no_logs += a["no_logs"]
            n_stale += a["stale"]
            n_ts_bad += (not a["ts_ok"] and not a["no_logs"])
        rows.sort(key=lambda a: (a["no_logs"], -a["size_bytes"]))
        n_no_platform += (not meta.get("prefix"))
        projects_out.append({
            "name": pname,
            "deploy_platform": meta.get("deploy_platform"),
            "prefix": meta.get("prefix"),
            "prefix_source": meta.get("source"),
            "no_prefix": not meta.get("prefix"),
            "apps": rows,
            "totals": {
                "apps": len(rows),
                "indices": sum(a["indices"] for a in rows),
                "size_bytes": sum(a["size_bytes"] for a in rows),
                "size_h": _hsize(sum(a["size_bytes"] for a in rows)),
                "docs": sum(a["docs"] for a in rows),
                "no_logs": sum(a["no_logs"] for a in rows),
                "ts_bad": sum(1 for a in rows if not a["ts_ok"] and not a["no_logs"]),
                "stale": sum(a["stale"] for a in rows),
            },
        })

    # any leftover apps under projects NOT in the inventory list
    leftover: dict = {}
    for (pname, app), rec in apps_model.items():
        leftover.setdefault(pname, []).append(_finalize_app(rec, stale_h))
    for pname, rows in leftover.items():
        for a in rows:
            a["not_in_inventory"] = True
            n_apps += 1
            tot["indices"] += a["indices"]
            tot["size_bytes"] += a["size_bytes"]
            tot["docs"] += a["docs"]
            all_envs.update(a["envs"])
            all_logtypes.update(a["logtypes"])
            n_stale += a["stale"]
            n_ts_bad += (not a["ts_ok"] and not a["no_logs"])
        projects_out.append({"name": pname, "not_in_inventory": True, "apps": rows,
                             "totals": {"apps": len(rows),
                                        "indices": sum(a["indices"] for a in rows),
                                        "size_bytes": sum(a["size_bytes"] for a in rows),
                                        "size_h": _hsize(sum(a["size_bytes"] for a in rows)),
                                        "docs": sum(a["docs"] for a in rows),
                                        "no_logs": sum(a["no_logs"] for a in rows),
                                        "ts_bad": sum(1 for a in rows if not a["ts_ok"] and not a["no_logs"]),
                                        "stale": sum(a["stale"] for a in rows)}})

    unmatched.sort(key=lambda u: -(u.get("size_bytes") or 0))
    # the platform→prefix legend for the platforms actually in use
    legend = sorted({(m.get("deploy_platform"), m.get("prefix"))
                     for m in proj_meta.values() if m.get("deploy_platform") and m.get("prefix")})
    summary = {
        "projects": len(projects_out), "apps": n_apps,
        "apps_no_logs": n_no_logs, "apps_stale": n_stale, "apps_ts_bad": n_ts_bad,
        "projects_no_platform": n_no_platform,
        "indices": tot["indices"], "size_bytes": tot["size_bytes"],
        "size_h": _hsize(tot["size_bytes"]), "docs": tot["docs"],
        "envs": sorted(all_envs), "logtypes": sorted(all_logtypes),
        "unmatched": len(unmatched),
    }
    return {"source": source, "note": note, "stale_hours": stale_h,
            "prefixes": sorted({m["prefix"] for m in proj_meta.values() if m.get("prefix")}),
            "platform_legend": [{"platform": pl, "prefix": pr} for pl, pr in legend],
            "connections": conn_status, "summary": summary,
            "projects": projects_out, "unmatched": unmatched[:50]}


def _known(projects: list[dict], prefixes) -> dict:
    return {
        "projects": [p["name"] for p in projects],
        "apps_by_project": {p["name"]: list(p.get("apps", [])) for p in projects},
        "apps": sorted({a for p in projects for a in p.get("apps", [])}),
        "envs": sorted({e for p in projects for e in p.get("envs", [])})
                or ["dev", "qc", "uat", "prd"],
        "prefixes": sorted(set(prefixes), key=len, reverse=True),
    }


def _project_prefixes(projects: list[dict]) -> dict:
    """Per project: resolve its log index prefix from `deploy_platform` via the
    configured map (OCP→oc, LinuxVM→vmlin, WindowsVM→vmwin), falling back to
    LOG_INDEX_PREFIX when a project doesn't declare a platform. Returns
    {project: {deploy_platform, prefix, source}} (source: platform|fallback|None)."""
    pmap = settings.log_platform_prefix_map
    fallback = (settings.log_index_prefix or "").strip()
    out = {}
    for p in projects:
        plat = (p.get("deploy_platform") or "").strip()
        pref = pmap.get(plat.lower()) if plat else None
        source = "platform" if pref else None
        if not pref and fallback:
            pref, source = fallback, "fallback"
        out[p["name"]] = {"deploy_platform": plat or None, "prefix": pref, "source": source}
    return out


# ------------------------------------------------------------------ live
def _live() -> dict:
    from . import inventory
    inv = inventory.parse()
    projects = inv.get("projects", [])
    if not projects:
        return {"source": "live", "prefixes": [], "platform_legend": [],
                "note": inv.get("note") or "no inventory projects — the "
                        "'inventories' repo isn't cloned/parsed yet.",
                "connections": {}, "summary": {}, "projects": [], "unmatched": []}
    proj_meta = _project_prefixes(projects)
    prefixes = sorted({m["prefix"] for m in proj_meta.values() if m["prefix"]})
    if not prefixes:
        return {"source": "live", "prefixes": [], "platform_legend": [],
                "note": "no log index prefix could be resolved — none of the "
                        "inventory projects declare a `deploy_platform` (OCP / "
                        "LinuxVM / WindowsVM) and QO_LOG_INDEX_PREFIX has no fallback.",
                "connections": {}, "summary": {}, "projects": [], "unmatched": []}
    known = _known(projects, prefixes)
    pattern = ",".join(f"{p}-*" for p in prefixes)   # union across the platforms

    conn_status: dict = {}
    records: list = []
    last_map: dict = {}
    for conn in _connections():
        kind = conn["kind"]
        if not conn["url"] or not conn["key"]:
            conn_status[kind] = {"label": conn["label"], "configured": False,
                                 "reachable": False, "indices": 0,
                                 "note": ("primary ES (ES_URL/ES_API_KEY) not configured"
                                          if kind == "prd" else
                                          "non-prd ES (ES_NONPRD_URL/ES_NONPRD_API_KEY) "
                                          "not configured — dev/qc/uat logs won't show")}
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
                conn_groups.setdefault((p["project"], p["app"]), []).append(name)
            records.append(rec)
        # newest @timestamp per app on this connection (one round trip)
        try:
            got = _msearch_max_ts(conn, [(f"{k[0]}\x00{k[1]}", v)
                                         for k, v in conn_groups.items()])
        except requests.RequestException:
            got = {}
        for k in conn_groups:
            iso = got.get(f"{k[0]}\x00{k[1]}")
            if iso:
                last_map.setdefault(k, []).append(iso)
        conn_status[kind] = {"label": conn["label"], "configured": True,
                             "reachable": True, "indices": len(cats), "url": conn["url"]}
        if unexpected:
            conn_status[kind]["unexpected_envs"] = sorted(unexpected)
    return _assemble(records, last_map, conn_status, projects, known, "live", proj_meta)


# ------------------------------------------------------------------ demo
def _iso_week(d: dt.datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}.{w:02d}"


def _demo() -> dict:
    from . import inventory
    projects = inventory.parse().get("projects", [])
    proj_meta = _project_prefixes(projects)   # Platform→oc, Control→vmlin, Research→vmwin
    prefixes = sorted({m["prefix"] for m in proj_meta.values() if m["prefix"]})
    known = _known(projects, prefixes)
    now = _now()
    weeks = [_iso_week(now - dt.timedelta(weeks=i)) for i in range(4)][::-1]  # oldest→newest
    logtypes = ["application", "access", "error"]
    records: list = []
    last_map: dict = {}
    # scripted quirks for a good story
    STALE = {("Platform", "notifications")}      # last log is old
    NO_LOGS = {("Control", "team-configs")}      # inventory app with no indices at all
    TS_BAD = {("Platform", "checkout")}          # @timestamp mapped as text in one env

    def env_conn(env):  # prd env served by prd connection, else non-prd
        return "prd" if env in set(settings.log_prd_env_list) else "nonprd"

    for p in projects:
        pname = p["name"]
        prefix = (proj_meta.get(pname) or {}).get("prefix")
        if not prefix:                           # no deploy_platform → no indices
            continue
        envs = p.get("envs", []) or ["dev", "prd"]
        for app in p.get("apps", []):
            key = (pname, app)
            if key in NO_LOGS:
                continue
            for env in envs:
                src = env_conn(env)
                for wk in weeks:
                    for lt in logtypes:
                        # smaller/rarer error indices; access+application every week
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
                                        "app": app, "logtype": lt, "week": wk})
            # last-logged: fresh for most, days-old for the stale one
            if key in STALE:
                iso = (now - dt.timedelta(days=5, hours=3)).replace(microsecond=0).isoformat() + "Z"
            else:
                iso = (now - dt.timedelta(minutes=6 + hash(app) % 40)).replace(microsecond=0).isoformat() + "Z"
            last_map.setdefault(key, []).append(iso)
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
    return _assemble(records, last_map, conn_status, projects, known, "demo", proj_meta)


# ------------------------------------------------------------------ public
def analyze(force: bool = False) -> dict:
    if not force and _CACHE["payload"] and time.time() - _CACHE["at"] < _TTL:
        return {**_CACHE["payload"], "cached": True}
    payload = _demo() if settings.demo_mode else _live()
    _CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}


def invalidate() -> None:
    _CACHE.update(at=0.0, payload=None)
