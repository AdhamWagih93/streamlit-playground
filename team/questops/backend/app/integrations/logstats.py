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


def _iso_week(d: dt.datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}.{w:02d}"


def _cur_isoweek() -> tuple[int, int]:
    y, w, _ = _now().isocalendar()
    return (y, w)


def _parse_week(week: str | None) -> tuple[int, int] | None:
    """'yyyy.ww' → (year, week) ints, or None if it isn't that shape."""
    try:
        y, w = (week or "").split(".")
        return (int(y), int(w))
    except (ValueError, AttributeError):
        return None


def _week_future(week: str | None, cur: tuple[int, int]) -> bool:
    """True when a well-formed yyyy.ww is later than the current ISO week."""
    yw = _parse_week(week)
    return bool(yw and yw > cur)


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
        # RARE pattern variant ${app}*-${logtype}: extra characters glued
        # DIRECTLY to the app name (payments2, checkoutv3-…) before the
        # -logtype part. Only tried when the exact token match fails, so the
        # normal pattern is unaffected. Longest app name that prefixes the
        # segment wins; the glued junk (up to the next '-' AFTER the app
        # name) is dropped and the remainder is the logtype.
        low = rest.lower()
        best = None
        for c in apps:
            cl = c.lower()
            if low.startswith(cl) and (best is None or len(cl) > len(best[1])):
                best = (c, cl)
        if best is None:
            return None
        app = best[0]
        cut = rest.find("-", len(best[1]))
        logtype = rest[cut + 1:].lstrip("-") if cut >= 0 else ""
        return {"prefix": prefix, "project": proj, "env": env.lower(), "app": app,
                "logtype": logtype or "—", "week": week, "bad_week": bad_week,
                "app_glued": rest[len(best[1]):cut if cut >= 0 else len(rest)]}
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
                     params={"ignore_unavailable": "true", "allow_no_indices": "true"},
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


def _grok_indices(conn: dict, pattern: str) -> dict:
    """{index: doc count} of indices holding docs tagged _grokparsefailure —
    Logstash grok patterns not matching, so the docs' fields are unusable.
    One cheap aggregation; best-effort ({} on any error)."""
    body = {"size": 0, "track_total_hits": False,
            "query": {"bool": {"should": [
                {"term": {"tags": "_grokparsefailure"}},
                {"term": {"tags.keyword": "_grokparsefailure"}}],
                "minimum_should_match": 1}},
            "aggs": {"idx": {"terms": {"field": "_index", "size": 2000}}}}
    try:
        r = requests.post(f"{conn['url']}/{pattern}/_search",
                          params={"ignore_unavailable": "true", "allow_no_indices": "true"},
                          json=body, headers=_headers(conn["key"]), timeout=30,
                          verify=conn["verify"])
        if not r.ok:
            return {}
        buckets = (((r.json().get("aggregations") or {}).get("idx") or {})
                   .get("buckets") or [])
        return {b["key"]: b.get("doc_count", 0) for b in buckets}
    except (requests.RequestException, ValueError):
        return {}


def _msearch_ts_range(conn: dict, groups: list[tuple]) -> dict:
    """One round trip: (first, last) @timestamp for each (key, [indices]) group
    — the oldest and newest logged document."""
    if not groups:
        return {}
    lines = []
    for _key, idxs in groups:
        lines.append(json.dumps({"index": ",".join(idxs)}))
        lines.append(json.dumps({"size": 0, "track_total_hits": False,
                                 "aggs": {"mn": {"min": {"field": "@timestamp"}},
                                          "mx": {"max": {"field": "@timestamp"}}}}))
    payload = "\n".join(lines) + "\n"
    r = requests.post(f"{conn['url']}/_msearch", data=payload,
                      headers={**_headers(conn["key"]),
                               "Content-Type": "application/x-ndjson"},
                      timeout=60, verify=conn["verify"])
    r.raise_for_status()
    resp = r.json().get("responses", [])
    out = {}
    for (key, _idxs), res in zip(groups, resp):
        aggs = (res or {}).get("aggregations") or {}
        out[key] = (_sane_ts(_ms_to_iso((aggs.get("mn") or {}).get("value"))),
                    _sane_ts(_ms_to_iso((aggs.get("mx") or {}).get("value"))))
    return out


def _sane_ts(iso: str | None) -> str | None:
    """Drop @timestamp aggregate values outside [2000, 2100] — junk docs
    (year 0001 / epoch 0 / five-digit years) otherwise poison the min/max
    logged span and every ingest-rate derived from it."""
    if not iso:
        return None
    try:
        return iso if 2000 <= int(iso[:4]) <= 2100 else None
    except ValueError:
        return None


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


# per-env health score deductions from 100 (higher = healthier); over_retained
# is MINOR; grok = docs tagged _grokparsefailure (fields unusable, moderate)
_PENALTY = {"stale": 50, "timestamp": 40, "bad_week": 20, "future_week": 20,
            "grok": 15, "over_retained": 10}
# the issue keys used across scoring + the issue-type filter
ISSUE_KEYS = ("no_logs", "stale", "timestamp", "bad_week", "future_week",
              "grok", "over_retained", "clash", "team_clash", "unsupported")


def _env_score(no_logs: bool, stale: bool, ts_bad: bool, bad_week: bool,
               future_week: bool, over_retained: bool, grok: bool = False) -> int:
    if no_logs:
        return 0
    s = 100 - (_PENALTY["stale"] if stale else 0) \
        - (_PENALTY["timestamp"] if ts_bad else 0) \
        - (_PENALTY["bad_week"] if bad_week else 0) \
        - (_PENALTY["future_week"] if future_week else 0) \
        - (_PENALTY["grok"] if grok else 0) \
        - (_PENALTY["over_retained"] if over_retained else 0)
    return max(s, 0)


def _finalize_app(rec: dict, stale_h: int, expected_envs, ts_map: dict,
                  meta: dict | None, env_meta: dict, deploy_map: dict) -> dict:
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

    prd_envs = set(settings.log_prd_env_list)
    ret_grace_days = 7   # tolerance before flagging over-retention

    by_env: dict = {}
    for i in idxs:
        by_env.setdefault(i.get("env") or "?", []).append(i)

    # env_stats covers every EXPECTED env (from inventory) ∪ envs seen in indices.
    # An env that was DEPLOYED but isn't logging = a real "no logs" issue; an env
    # never deployed is expected to have no logs (not an issue).
    env_stats = []
    for env in sorted(set(expected_envs or []) | set(by_env)):
        eidx = by_env.get(env, [])
        bad, _types = _ts_bad(eidx)
        rng = ts_map.get((pname, app, env), [])
        firsts = [f for f, _l in rng if f]
        lasts = [l for _f, l in rng if l]
        first = min(firsts) if firsts else None
        last = max(lasts) if lasts else None
        age = _age_hours(last)
        om = env_meta.get((pname, app, env)) or {}
        dep = deploy_map.get((pname, app, env)) or {}
        deployed = bool(dep)
        bad_week = sorted({i["index"] for i in eidx if i.get("bad_week")})
        future_week = sorted({i["index"] for i in eidx if i.get("future_week")})
        grok = sorted({i["index"] for i in eidx if i.get("grok_fail")})
        no_logs = len(eidx) == 0
        stale = (not no_logs) and (age is None or age > stale_h)
        ts_ok = (not no_logs) and not bad
        # over-retention: the OLDEST log is kept beyond the env's retention policy
        ret_days = settings.log_retention_prd_days if env in prd_envs \
            else settings.log_retention_nonprd_days
        first_age_h = _age_hours(first)
        over_retained = (not no_logs) and first_age_h is not None \
            and (first_age_h / 24.0) > (ret_days + ret_grace_days)
        issues = []
        if not monitored:
            score = None
        elif no_logs and not deployed:
            score = None                 # never deployed → expected, not scored
        elif no_logs:
            issues, score = ["no_logs"], 0
        else:
            if stale:
                issues.append("stale")
            if not ts_ok:
                issues.append("timestamp")
            if bad_week:
                issues.append("bad_week")
            if future_week:
                issues.append("future_week")
            if grok:
                issues.append("grok")
            if over_retained:
                issues.append("over_retained")
            score = _env_score(no_logs, stale, not ts_ok, bool(bad_week),
                               bool(future_week), over_retained, bool(grok))
        if om.get("clash"):
            issues.append("team_clash")
        env_stats.append({
            "env": env, "indices": len(eidx),
            "size_bytes": sum(i["size_bytes"] for i in eidx),
            "size_h": _hsize(sum(i["size_bytes"] for i in eidx)),
            "docs": sum(i["docs"] for i in eidx),
            "logtypes": sorted({i["logtype"] for i in eidx if i.get("logtype")}),
            "sources": sorted({i["source"] for i in eidx}),
            "retention_days": ret_days, "over_retained": over_retained,
            "first_logged": first, "last_logged": last, "last_logged_age_h": age,
            "no_logs": no_logs, "stale": stale, "ts_ok": ts_ok,
            "deployed": deployed, "last_deploy": dep.get("last_deploy"),
            "owner": om.get("owner"), "owner_app": om.get("app_owner"),
            "owner_project": om.get("project_owner"), "owner_clash": bool(om.get("clash")),
            "ts_bad_indices": sorted({x for v in bad.values() for x in v})[:10],
            "bad_week_indices": bad_week[:10], "future_week_indices": future_week[:10],
            "grok_indices": grok[:10],
            "issues": issues, "score": score})

    # app-level aggregates
    bad, types = _ts_bad(idxs)
    weeks = sorted({i["week"] for i in idxs if i.get("week") and not i.get("bad_week")})
    all_last = [s["last_logged"] for s in env_stats if s["last_logged"]]
    all_first = [s["first_logged"] for s in env_stats if s["first_logged"]]
    all_deploys = [s["last_deploy"] for s in env_stats if s["last_deploy"]]
    owner_clash_envs = [s["env"] for s in env_stats if s["owner_clash"]]
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
        "first_logged": min(all_first) if all_first else None,
        "last_logged": max(all_last) if all_last else None,
        "last_deploy": max(all_deploys) if all_deploys else None,
        "owners": sorted({s["owner"] for s in env_stats if s["owner"]}),
        "owner_clash_envs": owner_clash_envs,
        "ts_types": {t: len(v) for t, v in types.items()},
        "ts_bad_indices": sorted({x for v in bad.values() for x in v})[:25],
        "bad_week_indices": sorted({i["index"] for i in idxs if i.get("bad_week")})[:25],
        "future_week_indices": sorted({i["index"] for i in idxs if i.get("future_week")})[:25],
        "grok_indices": sorted({i["index"] for i in idxs if i.get("grok_fail")})[:25],
        "monitored": monitored, "platform_status": status,
        "env_stats": env_stats,
        # meta / platform fields
        "prefix": meta.get("prefix"), "deploy_platform": meta.get("deploy_platform"),
        "deploy_technology": meta.get("deploy_technology"),
        "tech_source": meta.get("tech_source"),
        "logging_required": meta.get("logging_required"),
        "company": meta.get("company"),
        "prefix_source": meta.get("source"),
        "app_platform": meta.get("app_platform"),
        "project_platform": meta.get("project_platform"),
        "discrepancy": meta.get("discrepancy", False),
    })
    rec["last_logged_age_h"] = _age_hours(rec["last_logged"])
    # deployment: an app is "deployed" if any of its envs was ever deployed
    rec["deployed"] = any(s["deployed"] for s in env_stats)
    rec["deployed_envs"] = sorted(s["env"] for s in env_stats if s["deployed"])
    rec["undeployed_envs"] = sorted(s["env"] for s in env_stats if not s["deployed"])
    rec["no_logs"] = monitored and len(idxs) == 0 and rec["deployed"]
    rec["ts_ok"] = len(idxs) > 0 and not bad
    rec["envs_stale"] = sum(1 for s in env_stats if s["stale"])
    # only DEPLOYED envs missing logs are real "no logs" problems
    rec["envs_no_logs"] = sum(1 for s in env_stats if monitored and s["no_logs"] and s["deployed"])
    rec["over_retained_envs"] = sorted(s["env"] for s in env_stats if s.get("over_retained"))
    rec["stale"] = rec["envs_stale"] > 0

    # per-app issue set (drives the issue-type filter) + health score
    issues = set()
    if not monitored and status == "unsupported":
        issues.add("unsupported")
    if monitored and rec["envs_no_logs"]:
        issues.add("no_logs")
    if rec["envs_stale"]:
        issues.add("stale")
    if len(idxs) > 0 and bad:
        issues.add("timestamp")
    if rec["bad_week_indices"]:
        issues.add("bad_week")
    if rec["future_week_indices"]:
        issues.add("future_week")
    if rec["grok_indices"]:
        issues.add("grok")
    if rec["over_retained_envs"]:
        issues.add("over_retained")
    if rec["discrepancy"]:
        issues.add("clash")
    if owner_clash_envs:
        issues.add("team_clash")
    rec["issues"] = sorted(issues)
    if not monitored:
        rec["score"] = None
    else:
        env_scores = [s["score"] for s in env_stats if s["score"] is not None]
        base = (sum(env_scores) / len(env_scores)) if env_scores else None
        if base is None:                  # monitored but no scored env (all un-deployed)
            rec["score"] = None
        else:
            if rec["discrepancy"]:
                base -= 10                # config-hygiene deductions
            if owner_clash_envs:
                base -= 10
            rec["score"] = max(int(round(base)), 0)

    rec["index_list"] = sorted(
        idxs, key=lambda x: (x.get("week") or "", x["index"]), reverse=True)[:60]
    return rec


def _assemble(records: list[dict], ts_map: dict, conn_status: dict,
              projects: list[dict], known: dict, source: str,
              app_meta: dict, proj_meta: dict, env_meta: dict | None = None,
              deploy_map: dict | None = None, note: str = "",
              diag: dict | None = None) -> dict:
    """Shape raw per-index records (+ per-app last-logged map) into the payload.
    `records` matched rows carry project/app/env/logtype/week; unmatched rows
    (unknown project/app) are collected separately. `app_meta` gives each
    (project, app) its effective deploy_platform + prefix + clash flag;
    `proj_meta` the project-global platform. Shared by live and demo."""
    stale_h = settings.log_stale_hours
    cur_yw = _cur_isoweek()          # detect the current ISO year.week from "now"
    env_meta = env_meta or {}
    deploy_map = deploy_map or {}
    apps_model: dict = {}
    unmatched: list = []
    for r in records:
        # a well-formed but FUTURE-dated index (year/week ahead of now) — usually
        # clock skew or a mis-templated index; distinct from a malformed bad_week
        r["future_week"] = (not r.get("bad_week")) and _week_future(r.get("week"), cur_yw)
        if r.get("app") and r.get("project"):
            key = (r["project"], r["app"])
            apps_model.setdefault(key, _blank_app(*key))["index_list"].append(r)
        else:
            unmatched.append({k: r[k] for k in ("index", "source", "size_bytes", "docs",
                                                "health", "ts_type", "week", "bad_week",
                                                "future_week") if k in r})
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
            "future_week": sum(1 for a in rows if "future_week" in a["issues"]),
            "over_retained": sum(1 for a in rows if "over_retained" in a["issues"]),
            "discrepancies": sum(1 for a in rows if "clash" in a["issues"]),
            "team_clash": sum(1 for a in rows if "team_clash" in a["issues"]),
            "unsupported": sum(1 for a in rows if "unsupported" in a["issues"]),
            "undeployed": sum(1 for a in rows if not a.get("deployed")),
        }

    def _fin(rec, pname, expected_envs, not_in_inv=False):
        a = _finalize_app(rec, stale_h, expected_envs, ts_map,
                          app_meta.get((pname, rec["app"])), env_meta, deploy_map)
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
            "company": meta.get("company"),
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

    # ---- storage vs the fleet AVERAGE (per app and per project) ----------
    # average over entities that actually store logs; >= factor × average =
    # "over_sized", a MINOR issue (-10) on the app / project score
    factor = settings.log_oversize_factor
    sized = [a["size_bytes"] for a in all_apps if a.get("monitored") and a["size_bytes"] > 0]
    app_avg = (sum(sized) / len(sized)) if sized else 0
    for a in all_apps:
        a["size_ratio"] = round(a["size_bytes"] / app_avg, 2) if app_avg and a["size_bytes"] else None
        a["over_sized"] = bool(app_avg and a["size_bytes"] >= app_avg * factor)
        if a["over_sized"]:
            a["issues"] = sorted(set(a["issues"]) | {"over_sized"})
            if a["score"] is not None:
                a["score"] = max(a["score"] - 10, 0)
    psized = [po["totals"]["size_bytes"] for po in projects_out if po["totals"]["size_bytes"] > 0]
    proj_avg = (sum(psized) / len(psized)) if psized else 0
    for po in projects_out:
        tb = po["totals"]["size_bytes"]
        po["size_ratio"] = round(tb / proj_avg, 2) if proj_avg and tb else None
        po["over_sized"] = bool(proj_avg and tb >= proj_avg * factor)
        po["score"] = _mean([a["score"] for a in po["apps"]])   # re-mean after app deductions
        if po["over_sized"] and po["score"] is not None:
            po["score"] = max(po["score"] - 10, 0)
        po["totals"]["over_sized"] = sum(1 for a in po["apps"] if "over_sized" in a["issues"])

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
        "unmatched_size_bytes": sum(u.get("size_bytes") or 0 for u in unmatched),
        "unmatched_size_h": _hsize(sum(u.get("size_bytes") or 0 for u in unmatched)),
        "overall_score": _mean([a["score"] for a in all_apps]),
        "apps_no_logs": cnt("no_logs"), "apps_stale": cnt("stale"),
        "apps_ts_bad": cnt("timestamp"), "apps_bad_week": cnt("bad_week"),
        "apps_future_week": cnt("future_week"), "apps_over_retained": cnt("over_retained"),
        "apps_grok": cnt("grok"),
        "apps_over_sized": cnt("over_sized"),
        "projects_over_sized": sum(1 for po in projects_out if po.get("over_sized")),
        "discrepancies": cnt("clash"), "apps_team_clash": cnt("team_clash"),
        "apps_unsupported": cnt("unsupported"),
        "apps_no_platform": sum(1 for a in all_apps if not a.get("prefix")),
        "apps_undeployed": sum(1 for a in all_apps if not a.get("deployed")),
        "projects_no_platform": sum(1 for po in projects_out if po.get("no_prefix")),
        "envs_stale": sum(a.get("envs_stale", 0) for a in all_apps),
        "envs_no_logs": sum(a.get("envs_no_logs", 0) for a in all_apps),
        "teams": sorted({o for a in all_apps for o in (a.get("owners") or [])}),
        "technologies": sorted({a.get("deploy_technology") for a in all_apps
                                if a.get("deploy_technology")}),
        "companies": sorted({a.get("company") for a in all_apps if a.get("company")}),
        "apps_logging_not_required": sum(1 for a in all_apps
                                         if a.get("logging_required") is False),
    }
    return {"source": source, "note": note, "stale_hours": stale_h,
            "current_week": _iso_week(_now()),
            "deploy_index": settings.log_deploy_index,
            "retention": {"prd_days": settings.log_retention_prd_days,
                          "nonprd_days": settings.log_retention_nonprd_days},
            "storage_avg": {"app_bytes": int(app_avg), "app_h": _hsize(int(app_avg)),
                            "project_bytes": int(proj_avg), "project_h": _hsize(int(proj_avg)),
                            "factor": factor},
            "env_order": {"main": settings.log_main_env_list,
                          "extra": settings.log_extra_env_list},
            "prefixes": sorted({m["prefix"] for m in app_meta.values() if m.get("prefix")}),
            "platform_legend": [{"platform": pl, "prefix": pr} for pl, pr in legend],
            "connections": conn_status, "summary": summary,
            "projects": projects_out, "unmatched": unmatched,
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


def _tech_logging() -> dict:
    """{technology_lower: logging-enabled bool} from the cloned Engine repo's
    vars/Deploy_Technologies/<technology>.yml files (`logging: true/false`).
    A technology with logging: false is NOT expected to ship logs. Missing
    repo/folder → {} (everything unknown). Demo fabricates a story."""
    if settings.demo_mode:
        return {"docker": True, "helm": True, "batch": False}
    from ..auth import _engine_dir
    d = _engine_dir()
    if not d:
        return {}
    folder = d / "vars" / "Deploy_Technologies"
    if not folder.is_dir():
        return {}
    import yaml
    out: dict = {}
    for f in list(folder.glob("*.yml")) + list(folder.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text()) or {}
        except Exception:  # noqa: BLE001 — one broken file must not kill the page
            continue
        v = data.get("logging") if isinstance(data, dict) else None
        if isinstance(v, str):
            v = v.strip().lower() in ("true", "yes", "1", "on")
        if v is not None:
            out[f.stem.lower()] = bool(v)
    return out


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
    tech_log = _tech_logging()
    app_meta: dict = {}
    proj_meta: dict = {}
    for p in projects:
        cfg = p.get("config") or {}
        pvars = cfg.get("project_vars") or {}
        avars = cfg.get("app_vars") or {}
        proj_plat = _clean(pvars.get("deploy_platform") or p.get("deploy_platform"))
        proj_company = _clean(pvars.get("company"))
        proj_meta[p["name"]] = {"deploy_platform": proj_plat, "company": proj_company,
                                "prefix": _platform_prefix(proj_plat, pmap)}
        proj_tech = _clean(pvars.get("deploy_technology"))
        for app in p.get("apps", []):
            app_plat = _clean((avars.get(app) or {}).get("deploy_platform"))
            # deploy_technology resolves the same way: app group_vars → project
            app_tech = _clean((avars.get(app) or {}).get("deploy_technology"))
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
                "deploy_platform": eff_plat, "company": proj_company,
                "deploy_technology": app_tech or proj_tech,
                "tech_source": "app" if app_tech else ("project" if proj_tech else None),
                "logging_required": tech_log.get((app_tech or proj_tech or "").lower()),
                "prefix": prefix, "source": src,
                "status": status, "app_platform": app_plat, "project_platform": proj_plat,
                "discrepancy": bool(app_plat and proj_plat
                                    and app_plat.lower() != proj_plat.lower())}
    return app_meta, proj_meta


def _env_teams(projects: list[dict]) -> tuple[dict, dict]:
    """The environment OWNER per (project, app, env), read from the inventory
    `${env}_team` var — app group_vars first (group_vars/<app>), else project
    group_vars/all. Returns (env_meta, proj_env):
      env_meta[(project, app, env)] = {owner, app_owner, project_owner, clash}
      proj_env[(project, env)] = project-level owner.
    `clash` = the app's env owner differs from the project's (assignment drift)."""
    env_meta: dict = {}
    proj_env: dict = {}
    for p in projects:
        cfg = p.get("config") or {}
        pvars = cfg.get("project_vars") or {}
        avars = cfg.get("app_vars") or {}
        for env in p.get("envs", []):
            proj_owner = _clean(pvars.get(f"{env}_team"))
            proj_env[(p["name"], env)] = proj_owner
            for app in p.get("apps", []):
                app_owner = _clean((avars.get(app) or {}).get(f"{env}_team"))
                env_meta[(p["name"], app, env)] = {
                    "owner": app_owner or proj_owner,
                    "app_owner": app_owner, "project_owner": proj_owner,
                    "clash": bool(app_owner and proj_owner
                                  and app_owner.lower() != proj_owner.lower())}
    return env_meta, proj_env


def _deployments(conn: dict, known: dict) -> tuple[dict, str | None]:
    """Last deployment date per (project, app, env) from the CI/CD deployments
    index (ef-cicd-deployments) on the prd ES. An (app, env) absent here was
    never deployed. Returns (deploy_map, error). Best-effort — never raises."""
    idx = (settings.log_deploy_index or "").strip()
    if not conn or not conn.get("url") or not conn.get("key") or not idx:
        return {}, None
    body = {"size": 0, "aggs": {"by": {
        "composite": {"size": 10000, "sources": [
            {"project": {"terms": {"field": "project"}}},
            {"application": {"terms": {"field": "application"}}},
            {"environment": {"terms": {"field": "environment"}}}]},
        "aggs": {"end": {"max": {"field": "enddate"}},
                 "start": {"max": {"field": "startdate"}}}}}}
    # only count REAL, SUCCESSFUL deployments: testflag skips test/dry-run
    # rows, status=SUCCESS skips failed/aborted runs (a failed deploy doesn't
    # make an environment "deployed")
    musts = []
    tf = (settings.log_deploy_testflag or "").strip()
    if tf:
        musts.append({"term": {"testflag": tf}})
    st = (settings.log_deploy_status or "").strip()
    if st:
        musts.append({"term": {"status": st}})
    if musts:
        body["query"] = musts[0] if len(musts) == 1 else {"bool": {"filter": musts}}
    try:
        r = requests.post(f"{conn['url']}/{idx}/_search", json=body,
                          headers=_headers(conn["key"]), timeout=30, verify=conn["verify"])
    except requests.RequestException as exc:
        return {}, str(exc)[:200]
    if not r.ok:
        return {}, f"HTTP {r.status_code} from {idx}"
    try:
        agg = ((r.json().get("aggregations") or {}).get("by") or {})
    except ValueError:
        return {}, "non-JSON response"
    proj_lc = {p.lower(): p for p in known["projects"]}
    app_lc = {a.lower(): a for a in known["apps"]}
    out: dict = {}
    for b in agg.get("buckets", []) or []:
        k = b.get("key") or {}
        proj = proj_lc.get(str(k.get("project", "")).lower())
        app = app_lc.get(str(k.get("application", "")).lower())
        env = str(k.get("environment", "")).strip().lower()
        if not proj or not app or not env:
            continue
        last = _ms_to_iso((b.get("end") or {}).get("value")) \
            or _ms_to_iso((b.get("start") or {}).get("value"))
        key = (proj, app, env)
        prev = out.get(key)
        cnt = (b.get("doc_count") or 0)
        if not prev:
            out[key] = {"last_deploy": last, "count": cnt}
        else:
            prev["count"] += cnt
            if (last or "") > (prev["last_deploy"] or ""):
                prev["last_deploy"] = last
    note = "deployment buckets truncated (>10k)" if agg.get("after_key") else None
    return out, note


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
    env_meta, _proj_env = _env_teams(projects) if projects else ({}, {})
    prefixes = sorted({m["prefix"] for m in app_meta.values() if m["prefix"]})
    diag = _inventory_diag(inv, projects, app_meta, proj_meta, prefixes)
    known = _known(projects, prefixes)
    # fetch *${prefix}-* (not just ${prefix}-*) so STRAY indices — a known
    # prefix buried mid-name (reindexes, restores, mis-templated shippers like
    # backup-oc-...) — are still pulled; _parse_index only matches the prefix
    # at the START, so those fall through to the "unmatched" (stray) list
    # instead of being invisible. "" when nothing resolved yet.
    pattern = ",".join(f"*{p}-*" for p in prefixes)
    # last deployment date per (project, app, env) — from the prd CI/CD index
    deploy_map, deploy_err = _deployments(_conn_by_kind("prd"), known)

    # ALWAYS probe both connections and report their real status — even when no
    # prefix resolves — so a configured ES never wrongly shows "off".
    conn_status: dict = {}
    records: list = []
    ts_map: dict = {}
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
            grok_map = _grok_indices(conn, pattern)
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
                   "health": row.get("health"), "ts_type": ts_types.get(name),
                   "grok_fail": name in grok_map,
                   "grok_docs": grok_map.get(name, 0)}
            if p:
                rec.update(p)
                if conn["expect_envs"] is not None and p["env"] not in conn["expect_envs"]:
                    unexpected.add(p["env"])
                elif conn["expect_envs"] is None and p["env"] in set(settings.log_prd_env_list):
                    unexpected.add(p["env"])
                conn_groups.setdefault((p["project"], p["app"], p["env"]), []).append(name)
            records.append(rec)
        # first + newest @timestamp per (app, ENV) on this connection (one round
        # trip) — staleness/coverage are judged per environment, not per app
        try:
            got = _msearch_ts_range(conn, [("\x00".join(k), v)
                                           for k, v in conn_groups.items()])
        except requests.RequestException:
            got = {}
        for k in conn_groups:
            rng = got.get("\x00".join(k))
            if rng:
                ts_map.setdefault(k, []).append(rng)
        conn_status[kind] = {"label": conn["label"], "configured": True,
                             "reachable": True, "indices": len(cats), "url": conn["url"]}
        if unexpected:
            conn_status[kind]["unexpected_envs"] = sorted(unexpected)
    if deploy_err and "prd" in conn_status:
        conn_status["prd"]["deploy_error"] = deploy_err

    note = ""
    if not projects:
        note = (inv.get("note") or "the 'inventories' repo isn't cloned/parsed yet — "
                "add & clone it on the Repositories page, then re-analyze.")
    elif not prefixes:
        note = ("no log index prefix resolved: no app or project declares a known "
                "`deploy_platform` (OCP / LinuxVM / WindowsVM / K8s) and "
                "QO_LOG_INDEX_PREFIX has no fallback. See the per-project/app detection below.")
    return _assemble(records, ts_map, conn_status, projects, known, "live",
                     app_meta, proj_meta, env_meta, deploy_map, note, diag)


# ------------------------------------------------------------------ demo
def _demo() -> dict:
    from . import inventory
    inv = inventory.parse()
    projects = inv.get("projects", [])
    app_meta, proj_meta = _app_meta(projects)
    env_meta, _proj_env = _env_teams(projects)
    prefixes = sorted({m["prefix"] for m in app_meta.values() if m["prefix"]})
    known = _known(projects, prefixes)
    diag = _inventory_diag(inv, projects, app_meta, proj_meta, prefixes)
    now = _now()
    weeks = [_iso_week(now - dt.timedelta(weeks=i)) for i in range(4)][::-1]  # oldest→newest
    logtypes = ["application", "access", "error"]
    records: list = []
    ts_map: dict = {}
    deploy_map: dict = {}
    # scripted quirks for a good story
    NO_LOGS = {("Control", "team-configs")}          # app with no indices at all
    TS_BAD = {("Platform", "checkout")}              # @timestamp = text in dev
    STALE_ENV = {("Platform", "notifications", "prd")}  # stale in PRD only
    MISSING_ENV = {("Platform", "payments", "qc")}   # payments not logging in qc
    BAD_WEEK = {("Platform", "checkout", "prd")}     # an illogical 5-digit year index
    FUTURE_WEEK = {("Platform", "checkout", "uat")}  # a future-dated index (clock skew)
    OVER_RETAINED = {("Platform", "notifications", "qc")}  # logs kept beyond retention
    GROK = {("Platform", "payments", "dev")}         # docs tagged _grokparsefailure
    # never deployed (→ expected to have no logs, hidden by default): the uat env
    # of notifications, and every env of team-configs
    UNDEPLOYED = {("Platform", "notifications", "uat")}
    future_wk = _iso_week(now + dt.timedelta(weeks=3))

    def env_conn(env):  # prd env served by prd connection, else non-prd
        return "prd" if env in set(settings.log_prd_env_list) else "nonprd"

    for p in projects:
        pname = p["name"]
        envs = p.get("envs", []) or ["dev", "prd"]
        for app in p.get("apps", []):
            key = (pname, app)
            prefix = (app_meta.get(key) or {}).get("prefix")   # per-app prefix
            # record deployments for every env EXCEPT the scripted un-deployed ones
            for env in envs:
                if key not in NO_LOGS and (pname, app, env) not in UNDEPLOYED:
                    deploy_map[(pname, app, env)] = {
                        "last_deploy": (now - dt.timedelta(days=2 + hash((app, env)) % 20))
                        .replace(microsecond=0).isoformat() + "Z", "count": 3 + hash(app) % 9}
            if key in NO_LOGS or not prefix:      # unsupported/none platform → not checked
                continue
            for env in envs:
                # not logging (deployed but broken) vs never deployed (expected)
                if (pname, app, env) in MISSING_ENV or (pname, app, env) in UNDEPLOYED:
                    continue
                src = env_conn(env)
                for wk in weeks:
                    for lt in logtypes:
                        if lt == "error" and (hash((app, env, wk)) % 3):
                            continue
                        size = 40_000_000 + (hash((app, env, wk, lt)) % 900) * 1_000_000
                        if app == "payments":     # scripted storage hog → over_sized story
                            size *= 4
                        docs = 50_000 + (hash((lt, app, wk)) % 800) * 1000
                        idx = f"{prefix}-{pname}-{env}-{app}-{lt}-{wk}"
                        ts_type = "text" if (key in TS_BAD and env == "dev"
                                             and lt == "application") else "date"
                        records.append({"index": idx, "source": src,
                                        "size_bytes": size, "docs": docs,
                                        "health": "green", "ts_type": ts_type,
                                        "grok_fail": (pname, app, env) in GROK
                                                     and lt == "application",
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
                # a well-formed but FUTURE-dated index → future_week issue
                if (pname, app, env) in FUTURE_WEEK:
                    records.append({"index": f"{prefix}-{pname}-{env}-{app}-application-{future_wk}",
                                    "source": src, "size_bytes": 4_000_000, "docs": 2500,
                                    "health": "green", "ts_type": "date", "prefix": prefix,
                                    "project": pname, "env": env, "app": app,
                                    "logtype": "application", "week": future_wk,
                                    "bad_week": False})
                # first + last logged PER ENV: fresh last, except the scripted
                # stale env; first ≈ oldest fabricated week, except the scripted
                # over-retained env (logs kept far beyond its retention policy)
                if (pname, app, env) in STALE_ENV:
                    last = (now - dt.timedelta(days=6, hours=2)).replace(microsecond=0).isoformat() + "Z"
                else:
                    last = (now - dt.timedelta(minutes=6 + hash((app, env)) % 40)).replace(microsecond=0).isoformat() + "Z"
                first_days = 120 if (pname, app, env) in OVER_RETAINED else 21
                first = (now - dt.timedelta(days=first_days, hours=hash((app, env)) % 12)).replace(microsecond=0).isoformat() + "Z"
                ts_map.setdefault((pname, app, env), []).append((first, last))
    # a few stray indices that carry a valid prefix but don't map to any known
    # app/project (naming drift → surfaced in the "unmatched" section). The
    # third has the prefix BURIED mid-name — caught by the *${prefix}-* fetch
    # pattern (a restore/reindex artifact), invisible under plain ${prefix}-*.
    for name in (f"oc-Platform-prd-legacy-batch-application-{weeks[-1]}",
                 f"vmwin-Sandbox-dev-scratch-debug-{weeks[-1]}",
                 f"restored-oc-Platform-prd-payments-application-{weeks[0]}"):
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
    return _assemble(records, ts_map, conn_status, projects, known, "demo",
                     app_meta, proj_meta, env_meta, deploy_map, diag=diag)


# ------------------------------------------------ @timestamp sample inspector
# a value is a plausible date only if it's an epoch (s or ms) landing in
# [2000, 2100] or an ISO/known date-string in the same range. Deliberately
# strict, so a text-mapped field's junk — bare number strings like
# "17840912390", out-of-range epochs, "N/A", "-" — is flagged as NOT a date.
_EPOCH_MIN = 946684800        # 2000-01-01
_EPOCH_MAX = 4102444800       # 2100-01-01
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _looks_date(v) -> bool:
    if v is None or isinstance(v, bool):
        return False
    num = None
    if isinstance(v, (int, float)):
        num = float(v)
    else:
        s = str(v).strip()
        if _NUMERIC_RE.match(s):
            try:
                num = float(s)
            except ValueError:
                num = None
        else:
            from .elastic import _parse_es_date   # ISO / known formats
            w = _parse_es_date(s)
            return bool(w and 2000 <= w.year <= 2100)
    if num is None:
        return False
    secs = num / 1000.0 if abs(num) >= 1e12 else num   # ms vs s
    return _EPOCH_MIN <= secs <= _EPOCH_MAX


def _looks_future(v) -> bool:
    """True when the @timestamp parses to a date suspiciously in the FUTURE
    (> 2 days ahead of now) — the doc-level cousin of the future-dated index
    check (clock skew / mis-templated loader)."""
    if v is None or isinstance(v, bool):
        return False
    num = None
    if isinstance(v, (int, float)):
        num = float(v)
    else:
        s = str(v).strip()
        if _NUMERIC_RE.match(s):
            try:
                num = float(s)
            except ValueError:
                return False
        else:
            from .elastic import _parse_es_date
            w = _parse_es_date(s)
            return bool(w and w > _now() + dt.timedelta(days=2))
    secs = num / 1000.0 if abs(num) >= 1e12 else num
    if not (_EPOCH_MIN <= secs <= _EPOCH_MAX):
        return False
    return dt.datetime.utcfromtimestamp(secs) > _now() + dt.timedelta(days=2)


def _conn_by_kind(kind: str) -> dict | None:
    for c in _connections():
        if c["kind"] == kind:
            return c
    return None


_ORIG_MAX = 4000     # cap event.original per doc so a huge message can't bloat the payload


def _orig_value(src: dict):
    """event.original from a doc _source (nested `event.original` or flattened),
    stringified and capped. Returns (value, truncated)."""
    v = src.get("event.original")
    if v is None:
        ev = src.get("event")
        if isinstance(ev, dict):
            v = ev.get("original")
    if v is None:
        return None, False
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    if len(s) > _ORIG_MAX:
        return s[:_ORIG_MAX], True
    return s, False


def _log_path(src: dict):
    """log.file.path from a doc _source (nested or flattened) — which log FILE
    on the host the troublesome doc was shipped from."""
    v = src.get("log.file.path")
    if v is None:
        lg = src.get("log")
        if isinstance(lg, dict):
            f = lg.get("file")
            if isinstance(f, dict):
                v = f.get("path")
            if v is None:
                v = lg.get("file.path")
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    return v if (v is None or isinstance(v, str)) else json.dumps(v, ensure_ascii=False)


def _fields_type(src: dict):
    """fields.type from a doc _source (nested or flattened) — the shipper's
    log-type tag on the troublesome doc."""
    v = src.get("fields.type")
    if v is None:
        fl = src.get("fields")
        if isinstance(fl, dict):
            v = fl.get("type")
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    return v if (v is None or isinstance(v, str)) else json.dumps(v, ensure_ascii=False)


def _sample_search(conn: dict, pattern: str, body: dict, docs: list, seen: set) -> str | None:
    """POST _search over a (multi-index) pattern, missing indices ignored;
    append parsed docs (deduped by index+id). Error string or None."""
    try:
        r = requests.post(f"{conn['url']}/{pattern}/_search",
                          params={"ignore_unavailable": "true", "allow_no_indices": "true"},
                          json=body, headers=_headers(conn["key"]), timeout=30, verify=conn["verify"])
    except requests.RequestException as exc:
        return str(exc)[:200]
    if not r.ok:
        return f"HTTP {r.status_code} from Elasticsearch"
    try:
        hits = (r.json().get("hits", {}) or {}).get("hits", []) or []
    except ValueError:
        return "non-JSON response"
    for h in hits:
        key = (h.get("_index"), h.get("_id"))
        if key in seen:
            continue
        seen.add(key)
        src = h.get("_source") or {}
        val = src.get("@timestamp")
        orig, trunc = _orig_value(src)
        tags = src.get("tags")
        if isinstance(tags, str):
            tags = [tags]
        docs.append({"index": h.get("_index"), "id": h.get("_id"), "value": val,
                     "is_date": _looks_date(val), "is_future": _looks_future(val),
                     "path": _log_path(src), "logtype": _fields_type(src),
                     "tags": tags if isinstance(tags, list) else None,
                     "original": orig, "original_truncated": trunc})
    return None


_GROK_QUERY = {"bool": {"should": [
    {"term": {"tags": "_grokparsefailure"}},
    {"term": {"tags.keyword": "_grokparsefailure"}}], "minimum_should_match": 1}}


def _sample_ts_multi(pattern: str, size: int, grok: bool = False) -> dict:
    """Sample @timestamp + event.original across ALL the given indices (a comma
    list of the app's suspect indices), on BOTH connections (missing ones
    ignored), tagging each @timestamp value as a plausible date or not. A
    targeted bare-number query surfaces the junk even when it's rare."""
    conns = [c for c in _connections() if c.get("url") and c.get("key")]
    if not conns:
        return {"indices": [i for i in pattern.split(",") if i],
                "error": "no Elasticsearch connection configured", "docs": []}
    src_fields = ["@timestamp", "event.original", "log.file.path", "fields.type", "tags"]
    docs: list = []
    seen: set = set()
    ts_types: dict = {}
    err = None
    for conn in conns:
        try:
            ts_types.update(_ts_field_types(conn, pattern))
        except requests.RequestException:
            pass
        if grok:   # only the docs that actually FAILED grok parsing
            e = _sample_search(conn, pattern, {"size": size, "_source": src_fields,
                "sort": "_doc", "query": _GROK_QUERY}, docs, seen)
        else:
            e = _sample_search(conn, pattern, {"size": size, "_source": src_fields,
                "sort": "_doc", "query": {"exists": {"field": "@timestamp"}}}, docs, seen)
            _sample_search(conn, pattern, {"size": max(6, size // 2), "_source": src_fields,
                "query": {"regexp": {"@timestamp.keyword": "-?[0-9]+([.][0-9]+)?"}}}, docs, seen)
        err = err or e
    # worst first: non-dates, then future-dated, then normal docs
    docs.sort(key=lambda d: (d["is_date"], not d.get("is_future")))
    docs = docs[:max(size, 25)]
    res = {"indices": sorted({d["index"] for d in docs}) or [i for i in pattern.split(",") if i],
           "ts_types": ts_types, "docs": docs,
           "non_date": sum(1 for d in docs if not d["is_date"]),
           "future": sum(1 for d in docs if d.get("is_future")), "sampled": len(docs)}
    if err and not docs:
        res["error"] = err
    return res


def ts_samples(index: str = "", source: str = "", good: str = "",
               good_source: str = "", size: int = 30, mode: str = "") -> dict:
    """`index` = comma-separated suspect indices — ALL of an app's
    @timestamp-bad ones, or its FUTURE-dated ones (mode="future"). Samples
    @timestamp + event.original across all of them (both ES connections),
    flagging docs whose @timestamp isn't a date / is in the future. The live
    sampling is identical either way; `mode` only shapes the demo payload."""
    idx = (index or "").strip()
    if not idx:
        raise ValueError("index is required")
    if settings.demo_mode:
        return _demo_ts_samples(idx, good, mode)
    out = {"index": _sample_ts_multi(idx, size, grok=(mode == "grok"))}
    if good.strip():
        out["good"] = _sample_ts_multi(good.strip(), size)
    return out


def _demo_ts_samples(index: str, good: str, mode: str = "") -> dict:
    now = _now()
    idxs = [i for i in index.split(",") if i][:3] or ["demo-index"]
    big = "<133>1 2026-08-05T10:12:03Z host app 4821 - [meta] " + ("lorem ipsum dolor sit amet " * 260)
    iso = lambda w: w.replace(microsecond=0).isoformat() + "Z"  # noqa: E731
    if mode == "future":
        # future-dated indices hold VALID dates — just from the future (clock
        # skew / a mis-templated loader), plus a couple of normal stragglers
        bad_vals = [iso(now + dt.timedelta(days=n)) for n in (365, 210, 92, 30, 30, 9)] \
            + [str(int((now + dt.timedelta(days=48)).timestamp()))] \
            + [iso(now - dt.timedelta(minutes=m)) for m in (12, 95)]
        ts_type = "date"
    elif mode == "grok":
        # grok-failed docs: timestamps fine, but the raw line didn't match any
        # grok pattern — fields never extracted
        bad_vals = [iso(now - dt.timedelta(minutes=m)) for m in (2, 9, 25, 44, 61, 90)]
        ts_type = "date"
    elif mode == "badweek":
        # bad-year indices: the INDEX NAME is malformed (mis-templated
        # yyyy.ww) — the docs themselves usually carry perfectly normal
        # timestamps; the value here is seeing WHICH file shipped them
        bad_vals = [iso(now - dt.timedelta(minutes=m)) for m in (3, 18, 41, 66, 120, 240, 300)]
        ts_type = "date"
    else:
        bad_vals = ["17840912390", "2026-08-05T10:12:03Z", "N/A", "1784091239012345",
                    "2026-08-05 10:11", "-", "pending", "2026-08-05T09:59:59Z",
                    "null", "0000-00-00", "1699999999"]
        ts_type = "text"
    demo_paths = ["/var/log/app/application.log", "/var/log/app/application.log.1",
                  "/opt/app/logs/batch-runner.log", "/var/log/messages"]
    bad = []
    for i, v in enumerate(bad_vals):
        raw = (big[:_ORIG_MAX] if i == 0 and mode != "future"
               else f"<134>1 {v} host svc - - raw event line for @timestamp={v!r}")
        if mode == "grok":
            raw = ("PAYMENT|%s|txn=%d|amount=EUR %d.%02d|state=OK|node=pay-0%d <unstructured tail>"
                   % (v, 91000 + i, 25 + i * 3, i * 7 % 100, i % 3 + 1))
        bad.append({"index": idxs[i % len(idxs)], "id": f"AbC{i}xY",
                    "value": v, "is_date": _looks_date(v), "is_future": _looks_future(v),
                    "path": demo_paths[i % len(demo_paths)],
                    "logtype": ("application", "access", "error")[i % 3],
                    "tags": ["_grokparsefailure", "beats_input"] if mode == "grok" else None,
                    "original": raw, "original_truncated": i == 0 and mode != "future"})
    bad.sort(key=lambda d: (d["is_date"], not d["is_future"]))
    res = {"index": {"indices": idxs, "ts_types": {ix: ts_type for ix in idxs},
                     "docs": bad, "non_date": sum(1 for d in bad if not d["is_date"]),
                     "future": sum(1 for d in bad if d["is_future"]),
                     "sampled": len(bad)}}
    if good.strip():
        gi = [i for i in good.split(",") if i][:1] or ["demo-good"]
        res["good"] = {"indices": gi, "ts_types": {gi[0]: "date"},
                       "docs": [{"index": gi[0], "id": f"G{i}", "value": (now - dt.timedelta(minutes=i * 7))
                                 .replace(microsecond=0).isoformat() + "Z", "is_date": True,
                                 "path": "/var/log/app/application.log", "logtype": "application",
                                 "original": f"<134>1 ... normal log line {i}", "original_truncated": False}
                                for i in range(6)], "non_date": 0, "sampled": 6}
    return res


# ------------------------------------------------------------------ public
def analyze(force: bool = False) -> dict:
    if not force and _CACHE["payload"] and time.time() - _CACHE["at"] < _TTL:
        return {**_CACHE["payload"], "cached": True}
    payload = _demo() if settings.demo_mode else _live()
    payload["analyzed_at"] = _now().replace(microsecond=0).isoformat() + "Z"
    _CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}


def invalidate() -> None:
    _CACHE.update(at=0.0, payload=None)
