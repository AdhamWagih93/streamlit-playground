"""Elasticsearch client for the Jenkins KPI index (30-min loader) and the
error-analysis index (categorized failures + AI verdicts). API-key auth.
Demo mode serves realistic fake documents."""

import datetime as dt

import requests

from ..config import settings


def _now() -> dt.datetime:
    return dt.datetime.now()


def is_live() -> bool:
    return bool(settings.es_url and settings.es_api_key and not settings.demo_mode)


def _search(index: str, body: dict) -> list[dict]:
    r = requests.post(f"{settings.es_url}/{index}/_search", json=body,
                      headers={"Authorization": f"ApiKey {settings.es_api_key}"},
                      timeout=20, verify=settings.es_verify_ssl)
    r.raise_for_status()
    return [h["_source"] for h in r.json().get("hits", {}).get("hits", [])]


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
def kpi_recent(hours: int = 24, size: int = 200) -> tuple[list[dict], bool]:
    """Returns (docs, window_applied). Tries the time window on @timestamp,
    then builddate; if both come back empty, falls back to the newest
    documents regardless of window so the panel is never silently blank."""
    if not is_live():
        return (_demo_kpi(), True) if settings.demo_mode else ([], True)
    for field in ("@timestamp", "builddate"):
        try:
            docs = _search(settings.jenkins_kpi_index, {
                "size": size,
                "query": {"range": {field: {"gte": f"now-{hours}h"}}},
                "sort": [{field: {"order": "desc", "unmapped_type": "date"}}],
            })
        except requests.HTTPError:
            continue
        if docs:
            return docs, True
    docs = _search(settings.jenkins_kpi_index, {
        "size": size, "query": {"match_all": {}},
        "sort": [{"@timestamp": {"order": "desc", "unmapped_type": "date"}}],
    })
    return docs, False


def error_analysis(days: int | None = None, size: int = 500) -> list[dict]:
    days = days or settings.error_analysis_days
    if is_live():
        return _search(settings.error_analysis_index, {
            "size": size,
            "query": {"range": {"Date": {"gte": f"now-{days}d"}}},
            "sort": [{"Date": {"order": "desc"}}],
        })
    return _demo_errors() if settings.demo_mode else []
