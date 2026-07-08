"""KPI-window hygiene + error-analysis views (Elasticsearch-backed).

/api/kpi     — countdown to the next KPI loader run and the failures that
               will be captured in it unless cleaned up first
/api/errors  — categorized Jenkins failures (TicketFlag / ErrorCode / AI verdicts)
"""

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..config import settings
from ..db import User
from ..integrations import elastic, jenkins

router = APIRouter(prefix="/api", tags=["insights"])

_FAILED = ("FAILURE", "FAILED", "UNSTABLE", "ABORTED")


@router.get("/kpi")
def kpi(hours: int = 24, user: User = Depends(current_user)):
    last_sync, next_sync = elastic.sync_times()
    now = elastic._now()

    # failures newer than the last loader run: they enter the KPI index on the
    # next run unless re-run green / cleaned up before the countdown hits zero
    since_last_min = (now - last_sync).total_seconds() / 60
    ci = jenkins.overview()
    at_risk = [f for f in ci["failures"] if f["ago_min"] <= since_last_min]

    try:
        recent, window_applied = elastic.kpi_recent(hours=hours)
        es_error = None
    except Exception as exc:  # noqa: BLE001 — surface ES problems in the panel
        recent, window_applied, es_error = [], True, str(exc)[:300]
    loaded_failures = [d for d in recent
                       if str(d.get("status", "")).upper() in _FAILED]

    return {
        "sync_marks": settings.kpi_sync_marks,
        "last_sync": last_sync.isoformat(),
        "next_sync": next_sync.isoformat(),
        "seconds_remaining": max(0, int((next_sync - now).total_seconds())),
        "at_risk": at_risk,
        "hours": hours,
        "loaded": recent[:100],  # the actual KPI documents, newest first
        "loaded_failures": loaded_failures[:25],
        "loaded_total": len(recent),
        "window_applied": window_applied,
        "es_error": es_error,
        "index": settings.jenkins_kpi_index,
        "source": "live" if elastic.is_live() else "demo",
    }


@router.get("/errors")
def errors(days: int = 0, user: User = Depends(current_user)):
    try:
        docs = elastic.error_analysis(days or None)
        es_error = None
    except Exception as exc:  # noqa: BLE001
        docs, es_error = [], str(exc)[:300]
    flags = sorted({d.get("TicketFlag") or "Unflagged" for d in docs})
    return {"errors": docs, "flags": flags, "es_error": es_error,
            "days": days or settings.error_analysis_days,
            "source": "live" if elastic.is_live() else "demo"}
