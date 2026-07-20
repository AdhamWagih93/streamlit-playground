"""Elasticsearch client for the Jenkins KPI index (30-min loader) and the
error-analysis index (categorized failures + AI verdicts). API-key auth.
Demo mode serves realistic fake documents."""

import datetime as dt
import re

import requests

from ..config import settings


def _now() -> dt.datetime:
    return dt.datetime.now()


def is_live() -> bool:
    return bool(settings.es_url and settings.es_api_key and not settings.demo_mode)


def _es_reason(resp) -> str:
    """The human-readable reason Elasticsearch put in a 4xx/5xx body — the part
    that actually explains a 400 (e.g. 'Text fields are not optimised for
    sorting', 'failed to parse date field [now-168h]'). raise_for_status only
    gives the status line + URL, so we dig the reason out ourselves."""
    try:
        err = (resp.json() or {}).get("error")
    except ValueError:
        return (resp.text or "").strip()[:200]
    if isinstance(err, str):
        return err[:200]
    if isinstance(err, dict):
        root = err.get("root_cause") or []
        if root and isinstance(root, list) and isinstance(root[0], dict) and root[0].get("reason"):
            return str(root[0]["reason"])[:200]
        if err.get("reason"):
            return str(err["reason"])[:200]
    return ""


def _search_hits(index: str, body: dict) -> tuple[list[dict], int]:
    """(docs, true total matching the query) — the total makes truncation
    visible instead of silent."""
    r = requests.post(f"{settings.es_url}/{index}/_search", json=body,
                      headers={"Authorization": f"ApiKey {settings.es_api_key}"},
                      timeout=30, verify=settings.es_verify_ssl)
    try:
        r.raise_for_status()
    except requests.HTTPError as exc:
        # attach ES's own explanation — otherwise a 400 is an opaque status line
        reason = _es_reason(r)
        if reason:
            raise requests.HTTPError(f"{exc} — Elasticsearch: {reason}",
                                     response=r) from None
        raise
    hits = r.json().get("hits", {})
    total = hits.get("total", {})
    total_n = total.get("value", 0) if isinstance(total, dict) else int(total or 0)
    return [h["_source"] for h in hits.get("hits", [])], total_n


def _search(index: str, body: dict) -> list[dict]:
    return _search_hits(index, body)[0]


def _search_hits_relaxed(index: str, body: dict) -> tuple[list[dict], int, bool]:
    """(docs, total, sorted_ok). Sorting on a text-mapped field 400s the
    whole query — retry WITHOUT the sort. sorted_ok=False means the docs are
    in ARBITRARY (usually insertion = oldest-first) order: an unsorted,
    truncated fetch must never be trusted to contain the newest data."""
    try:
        docs, total = _search_hits(index, body)
        return docs, total, True
    except requests.HTTPError:
        if "sort" not in body:
            raise
        stripped = {k: v for k, v in body.items() if k != "sort"}
        docs, total = _search_hits(index, stripped)
        return docs, total, False


# ---------------------------------------------------------------- KPI loader schedule
def sync_times() -> tuple[dt.datetime, dt.datetime]:
    """(last_sync, next_sync) based on the configured minute marks.
    Uses container-local time — set TZ to match the loader's timezone."""
    now = _now()
    nexts, lasts = [], []
    for m in settings.kpi_sync_marks:
        mark = now.replace(minute=m, second=0, microsecond=0)
        nexts.append(mark + dt.timedelta(hours=1) if mark <= now else mark)
        lasts.append(mark - dt.timedelta(hours=1) if mark > now else mark)
    return max(lasts), min(nexts)


# ---------------------------------------------------------------- demo docs
def _demo_kpi() -> list[dict]:
    now = _now()
    rows = [
        ("payments-service/main", 481, "FAILURE", 42, "SCM", "push by bob"),
        ("checkout-service/main", 902, "FAILURE", 130, "SCM", "push by carol"),
        ("inventory-service/main", 764, "SUCCESS", 55, "TIMER", "nightly"),
        ("auth-service/main", 512, "SUCCESS", 20, "SCM", "push by dave"),
        ("platform-terraform/apply", 233, "UNSTABLE", 300, "UPSTREAM", "platform-terraform/plan"),
        ("notifications-service/main", 289, "SUCCESS", 400, "SCM", "push by alice"),
    ]
    return [{
        "@timestamp": (now - dt.timedelta(minutes=m)).isoformat(),
        "builddate": (now - dt.timedelta(minutes=m)).isoformat(),
        "buildnumber": str(num), "buildurl": f"#demo/jenkins/{job}/{num}",
        "jobname": job.split("/")[-1], "jobpath": job, "joburl": f"#demo/jenkins/{job}",
        "status": status, "depth": "2", "triggerbuildnumber": "",
        "triggeredby": by, "triggertype": ttype, "unid": f"demo-{num}",
    } for job, num, status, m, ttype, by in rows]


def _demo_errors() -> list[dict]:
    now = _now()
    rows = [
        ("payments-service/main", "GIT-AUTH-401", "Infrastructure", "Renew git credentials on agent",
         "Ticket Required", "0.94", 1,
         "Authentication to the SCM failed during checkout; credential id 'git-prd' returned 401."),
        ("checkout-service/main", "MVN-COMPILE-001", "Code", "Fix compilation error in OrderService.java",
         "Ticket Required", "0.98", 3,
         "Compilation failed: OrderService.java:214 incompatible types after dependency bump."),
        ("platform-terraform/apply", "TF-LOCK-409", "Infrastructure", "Release stale terraform state lock",
         "Known Issue", "0.88", 6,
         "State lock held by a previous aborted run; releasing the lock and retrying resolves it."),
        ("data-warehouse/nightly-etl", "K8S-TIMEOUT-504", "Flaky Infrastructure", "Retry build",
         "No Action", "0.81", 26,
         "Pod scheduling timed out during a node-pool scale-up; transient, succeeded on retry."),
        ("monolith/regression-suite", "TEST-FLAKY-017", "Flaky Test", "Quarantine test CheckoutFlowIT",
         "Known Issue", "0.90", 50,
         "CheckoutFlowIT failed with a timing assertion; 4th occurrence this month, same stack."),
        ("auth-service/main", "DOCKER-PUSH-503", "Infrastructure", "Check registry availability",
         "Ticket Required", "0.86", 75,
         "Image push failed: registry returned 503 for 3 consecutive attempts."),
    ]
    return [{
        "Date": (now - dt.timedelta(hours=h)).isoformat(),
        "jobname": job.split("/")[-1], "jobpath": job, "buildurl": f"#demo/jenkins/{job}",
        "ErrorCode": code, "ErrorType": etype, "ErrorAction": action, "TicketFlag": flag,
        "AIErrorCode": code, "AIErrorType": etype, "AIErrorAction": action,
        "AITicketFlag": flag, "AIConfidence": conf, "AIRaw": raw,
    } for job, code, etype, action, flag, conf, h, raw in rows]


# ---------------------------------------------------------------- public API
def _parse_es_date(val) -> dt.datetime | None:
    """Best-effort doc-date parsing: ISO (any tz), epoch s/ms, and the usual
    non-ISO loader formats. Needed because the index's date fields are not
    always date-mapped — ES range queries silently match nothing then."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            ts = float(val)
            if ts > 1e12:
                ts /= 1000.0
            return dt.datetime.utcfromtimestamp(ts)
        except (ValueError, OverflowError, OSError):
            return None
    s = str(val).strip()
    if not s:
        return None
    if s.isdigit():
        return _parse_es_date(int(s))
    try:
        iso = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", s.replace("Z", "+00:00"))
        parsed = dt.datetime.fromisoformat(iso)
        return (parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
                if parsed.tzinfo else parsed)
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y @ %I:%M:%S %p",   # 14-Jul-2026 @ 04:59:41 PM (Jenkins KPI loader)
                "%d-%b-%Y %I:%M:%S %p", "%d-%b-%Y %H:%M:%S", "%d %b %Y %H:%M:%S",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%d.%m.%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _doc_when(doc: dict) -> dt.datetime | None:
    """The build time, trying KPI_DATE_FIELDS in order (default: builddate —
    when the build RAN — then @timestamp, the loader's ingest time)."""
    for f in settings.kpi_date_field_list:
        w = _parse_es_date(doc.get(f))
        if w is not None:
            return w
    return None


def _kpi_ignored(doc: dict) -> bool:
    """KPI_IGNORE: same substring semantics as JENKINS_IGNORE, own knob."""
    if not settings.kpi_ignore_tokens:
        return False
    hay = f"{doc.get('jobpath') or ''}/{doc.get('jobname') or ''}".lower()
    return any(tok in hay for tok in settings.kpi_ignore_tokens)


def _apply_kpi_ignore(docs: list[dict], total: int) -> tuple[list[dict], int, int]:
    """(kept docs, adjusted total, ignored count). The total adjustment is
    exact whenever the fetch wasn't truncated (the normal case)."""
    if not settings.kpi_ignore_tokens:
        return docs, total, 0
    kept = [d for d in docs if not _kpi_ignored(d)]
    ignored = len(docs) - len(kept)
    return kept, max(total - ignored, len(kept)), ignored


def _window_days(hours: int, now: dt.datetime, cap: int = 92) -> list[dt.datetime]:
    days = min(int(hours // 24) + 2, cap)
    return [now - dt.timedelta(days=i) for i in range(days)]


def _kpi_query_tiers(hours: int, now: dt.datetime) -> list[tuple[str, dict, str]]:
    """(name, query, sort_field) tiers, most precise first. Range works on
    date-mapped fields; day-PHRASE queries window TEXT-mapped fields
    ('14-Jul-2026' analyzes to adjacent terms); day-prefix WILDCARDS window
    KEYWORD-mapped fields; match_all is the last resort."""
    fields = settings.kpi_date_field_list
    tiers: list[tuple[str, dict, str]] = []
    for f in fields:  # first field = when the build RAN
        tiers.append((f"range:{f}",
                      {"range": {f: {"gte": f"now-{hours}h"}}}, f))
    days = _window_days(hours, now)
    if int(hours // 24) + 2 <= 92:  # day-enumeration only for sane windows
        phrases = [d.strftime("%d-%b-%Y") for d in days]
        for f in fields:
            tiers.append((f"day-phrase:{f}",
                          {"bool": {"should": [{"match_phrase": {f: p}} for p in phrases],
                                    "minimum_should_match": 1}}, f))
        for f in fields:
            tiers.append((f"day-wildcard:{f}",
                          {"bool": {"should": [{"wildcard": {f: {"value": p + "*"}}}
                                               for p in phrases],
                                    "minimum_should_match": 1}}, f))
    tiers.append(("match_all", {"match_all": {}}, "@timestamp"))
    return tiers


def kpi_recent(hours: int = 168, size: int | None = None) -> dict:
    """Docs for the whole time window — the past week by default, or the UI's
    time filter. Walks query tiers until one yields in-window docs; the
    window is ALWAYS re-checked client-side on parsed dates (builddate
    first — re-ingested old builds carry a fresh @timestamp). An UNSORTED
    truncated fetch (text-mapped sort failures return oldest-first) is never
    allowed to conclude 'no recent data' — that's what zeroed the panel.
    Returns {docs, window_applied, window_source, total, ignored,
    fetch_truncated, debug}."""
    size = size or settings.kpi_max_docs
    if not is_live():
        docs = _demo_kpi() if settings.demo_mode else []
        docs, total, ignored = _apply_kpi_ignore(docs, len(docs))
        return {"docs": docs, "window_applied": True, "window_source": "demo",
                "total": total, "ignored": ignored, "fetch_truncated": False,
                "newest_at": None, "debug": None}

    # container-local now: the loader writes local-time strings and TZ is
    # configured to match it (same clock the sync countdown uses)
    now = _now()
    cutoff = now - dt.timedelta(hours=hours)
    chosen, fallback, attempts = None, None, []
    date_typed: set[str] = set()   # fields a range tier queried cleanly
    seen_fields: list[str] = []     # top-level keys of a real doc (diagnostics)
    for name, query, sfield in _kpi_query_tiers(hours, now):
        # the day-phrase/day-wildcard tiers exist for TEXT-mapped date fields;
        # on a real date field they only 400 — skip them once range has shown
        # the field is date-mapped (range covers it authoritatively).
        if name.startswith(("day-phrase", "day-wildcard")) and sfield in date_typed:
            attempts.append(f"{name}: skipped — {sfield} is date-mapped (range covers it)")
            continue
        try:
            raw, total_raw, sorted_ok = _search_hits_relaxed(
                settings.jenkins_kpi_index,
                {"size": size, "track_total_hits": True, "query": query,
                 "sort": [{sfield: {"order": "desc", "unmapped_type": "date"}}]})
        except requests.HTTPError as exc:
            # keep the FULL message (URL + ES reason) — a 60-char cut used to
            # chop it mid-port and read like a wrong port (":8383" -> ":8")
            attempts.append(f"{name}: HTTP error {str(exc)[:280]}")
            continue
        if name.startswith("range"):
            date_typed.add(sfield)  # range accepted date-math => field is a date
        if raw and not seen_fields:
            seen_fields = sorted(raw[0].keys())
        if not raw:
            attempts.append(f"{name}: 0 hits")
            continue
        # window value from the tier's OWN field: a range:@timestamp tier must
        # be judged on @timestamp, not silently overruled by a stale builddate
        # (re-ingested builds keep an old builddate) — that used to zero the
        # panel even when @timestamp was fresh.
        dated = [(d, _parse_es_date(d.get(sfield)) or _doc_when(d)) for d in raw]
        any_parsed = any(w is not None for _, w in dated)
        if name.startswith("range"):
            kept = dated  # ES already applied the window on a date-mapped field
        elif any_parsed:
            kept = [(d, w) for d, w in dated if w is not None and w >= cutoff]
        else:
            kept = []
        attempts.append(f"{name}: {len(raw)} fetched (total {total_raw}, "
                        f"sorted={sorted_ok}), {len(kept)} in window")
        result = (name, raw, total_raw, sorted_ok, dated, kept, any_parsed)
        if kept:
            chosen = result
            break
        # 0 in window from a SORTED newest-first fetch is a legitimate
        # conclusion; from an unsorted/truncated one it proves nothing
        if any_parsed and sorted_ok and total_raw <= len(raw) and name != "match_all":
            chosen = result
            break
        fallback = fallback or result

    if chosen is None and fallback is None:  # nothing anywhere
        return {"docs": [], "window_applied": True, "window_source": "none",
                "total": 0, "ignored": 0, "fetch_truncated": False,
                "newest_at": None,
                "debug": {"attempts": attempts, "sample": [],
                          "doc_fields": seen_fields, "date_like_fields": [],
                          "configured_date_fields": settings.kpi_date_field_list,
                          "server_now": now.isoformat()}}

    name, raw, total_raw, sorted_ok, dated, kept, any_parsed = chosen or fallback
    if chosen is None and not any_parsed:
        kept, window_applied, window_source = dated, False, "none"
    else:
        window_applied = True
        window_source = name if chosen else f"{name} (unconfirmed window)"

    kept.sort(key=lambda t: t[1] or dt.datetime.min, reverse=True)
    docs = [d for d, _ in kept]
    # for a range tier ES already counted the window authoritatively
    total = total_raw if name.startswith("range") else (len(docs) if any_parsed else total_raw)
    fetch_truncated = total_raw > len(raw)
    docs, total, ignored = _apply_kpi_ignore(docs, total)
    sample = [{"builddate": d.get("builddate"), "@timestamp": d.get("@timestamp"),
               "parsed": w is not None} for d, w in dated[:3]]
    # newest build actually in the index (build-time first) — lets the UI say
    # "no builds in the last Xh; newest is Yd ago — widen the window"
    build_times = [b for b in (_doc_when(d) for d in raw) if b is not None]
    newest_at = max(build_times).isoformat() if build_times else None
    # every field in a real doc whose value parses as a PLAUSIBLE date (year
    # >= 2000, so small ints like buildnumber don't masquerade as 1970 epochs)
    # — makes a mis-named build-time field obvious
    def _is_datey(v):
        w = _parse_es_date(v)
        return w is not None and w.year >= 2000
    date_like = sorted(k for k, v in (raw[0] if raw else {}).items() if _is_datey(v))
    return {"docs": docs, "window_applied": window_applied,
            "window_source": window_source, "total": total,
            "ignored": ignored, "fetch_truncated": fetch_truncated,
            "newest_at": newest_at,
            "debug": {"attempts": attempts, "sample": sample,
                      "doc_fields": seen_fields, "date_like_fields": date_like,
                      "configured_date_fields": settings.kpi_date_field_list,
                      "server_now": now.isoformat()}}


def error_analysis(days: int | None = None, size: int = 500) -> list[dict]:
    days = days or settings.error_analysis_days
    if is_live():
        return _search(settings.error_analysis_index, {
            "size": size,
            "query": {"range": {"Date": {"gte": f"now-{days}d"}}},
            "sort": [{"Date": {"order": "desc"}}],
        })
    return _demo_errors() if settings.demo_mode else []
