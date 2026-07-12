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


def _search_hits(index: str, body: dict) -> tuple[list[dict], int]:
    """(docs, true total matching the query) — the total makes truncation
    visible instead of silent."""
    r = requests.post(f"{settings.es_url}/{index}/_search", json=body,
                      headers={"Authorization": f"ApiKey {settings.es_api_key}"},
                      timeout=30, verify=settings.es_verify_ssl)
    r.raise_for_status()
    hits = r.json().get("hits", {})
    total = hits.get("total", {})
    total_n = total.get("value", 0) if isinstance(total, dict) else int(total or 0)
    return [h["_source"] for h in hits.get("hits", [])], total_n


def _search(index: str, body: dict) -> list[dict]:
    return _search_hits(index, body)[0]


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
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%d.%m.%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _doc_when(doc: dict) -> dt.datetime | None:
    """builddate first — it is when the build RAN; @timestamp is only when
    the loader ingested it (re-ingested old builds have a recent one)."""
    return _parse_es_date(doc.get("builddate")) or _parse_es_date(doc.get("@timestamp"))


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


def kpi_recent(hours: int = 168, size: int | None = None) -> dict:
    """Docs for the whole time window — the past week by default, or the UI's
    time filter. The window is ALWAYS enforced client-side on parsed doc
    dates (builddate first) even when the ES range query worked, because:
    (a) non-date-mapped fields make ES ranges silently match nothing, and
    (b) re-ingested old builds carry a fresh @timestamp but an old builddate.
    Returns {docs, window_applied, window_source, total, ignored, fetch_truncated}."""
    size = size or settings.kpi_max_docs
    if not is_live():
        docs = _demo_kpi() if settings.demo_mode else []
        docs, total, ignored = _apply_kpi_ignore(docs, len(docs))
        return {"docs": docs, "window_applied": True, "window_source": "demo",
                "total": total, "ignored": ignored, "fetch_truncated": False}

    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=hours)
    raw, total_raw, source = [], 0, "none"
    for field in ("builddate", "@timestamp"):  # builddate = when the build RAN
        try:
            docs, total = _search_hits(settings.jenkins_kpi_index, {
                "size": size, "track_total_hits": True,
                "query": {"range": {field: {"gte": f"now-{hours}h"}}},
                "sort": [{field: {"order": "desc", "unmapped_type": "date"}}],
            })
        except requests.HTTPError:
            continue
        if docs:
            raw, total_raw, source = docs, total, "es"
            break
    if not raw:  # range matched nothing (likely text-mapped dates) — fetch newest
        raw, total_raw = _search_hits(settings.jenkins_kpi_index, {
            "size": size, "track_total_hits": True, "query": {"match_all": {}},
            "sort": [{"@timestamp": {"order": "desc", "unmapped_type": "date"}}],
        })

    dated = [(d, _doc_when(d)) for d in raw]
    any_parsed = any(w is not None for _, w in dated)
    if source == "es":
        # trust ES's window, but still drop re-ingested old builds
        kept = [(d, w) for d, w in dated if w is None or w >= cutoff]
        window_applied = True
        window_source = "es+client" if len(kept) < len(dated) else "es"
    elif any_parsed:
        kept = [(d, w) for d, w in dated if w is not None and w >= cutoff]
        window_applied, window_source = True, "client"
    else:  # no parseable dates at all — show newest, clearly flagged
        kept, window_applied, window_source = dated, False, "none"

    kept.sort(key=lambda t: t[1] or dt.datetime.min, reverse=True)
    docs = [d for d, _ in kept]
    dropped = len(raw) - len(docs)
    total = max(total_raw - dropped, len(docs)) if source == "es" else len(docs)
    fetch_truncated = total_raw > len(raw)  # the fetch cap hid part of the window
    docs, total, ignored = _apply_kpi_ignore(docs, total)
    return {"docs": docs, "window_applied": window_applied,
            "window_source": window_source, "total": total,
            "ignored": ignored, "fetch_truncated": fetch_truncated}


def error_analysis(days: int | None = None, size: int = 500) -> list[dict]:
    days = days or settings.error_analysis_days
    if is_live():
        return _search(settings.error_analysis_index, {
            "size": size,
            "query": {"range": {"Date": {"gte": f"now-{days}d"}}},
            "sort": [{"Date": {"order": "desc"}}],
        })
    return _demo_errors() if settings.demo_mode else []
