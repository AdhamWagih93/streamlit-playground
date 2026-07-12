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


def _es_source() -> str:
    return "live" if elastic.is_live() else ("demo" if settings.demo_mode else "not configured")


@router.get("/kpi")
def kpi(hours: int = 168, user: User = Depends(current_user)):
    """Default window: the past week; `hours` follows the UI time filter."""
    last_sync, next_sync = elastic.sync_times()
    now = elastic._now()

    # failures newer than the last loader run: they enter the KPI index on the
    # next run unless re-run green / cleaned up before the countdown hits zero
    since_last_min = (now - last_sync).total_seconds() / 60
    ci = jenkins.overview()
    at_risk = [f for f in ci["failures"] if f["ago_min"] <= since_last_min]

    try:
        recent, window_applied, total_in_window = elastic.kpi_recent(hours=hours)
        es_error = None
    except Exception as exc:  # noqa: BLE001 — surface ES problems in the panel
        recent, window_applied, total_in_window, es_error = [], True, 0, str(exc)[:300]
    loaded_failures = [d for d in recent
                       if str(d.get("status", "")).upper() in _FAILED]

    # success percentages: overall and per pipeline, worst first
    def _pct(ok: int, total: int) -> float:
        return round(ok / total * 100, 1) if total else 0.0

    by_job: dict[str, dict] = {}
    ok_total = 0
    for d in recent:
        job = d.get("jobpath") or d.get("jobname") or "?"
        row = by_job.setdefault(job, {"job": job, "total": 0, "success": 0})
        row["total"] += 1
        if str(d.get("status", "")).upper() == "SUCCESS":
            row["success"] += 1
            ok_total += 1
    stats = {
        "total": len(recent),
        "success": ok_total,
        "overall_pct": _pct(ok_total, len(recent)),
        "pipelines": sorted(
            ({**r, "pct": _pct(r["success"], r["total"])} for r in by_job.values()),
            key=lambda r: (r["pct"], -r["total"])),
    }

    return {
        "stats": stats,
        "sync_marks": settings.kpi_sync_marks,
        "last_sync": last_sync.isoformat(),
        "next_sync": next_sync.isoformat(),
        "seconds_remaining": max(0, int((next_sync - now).total_seconds())),
        "at_risk": at_risk,
        "hours": hours,
        "loaded": recent[:100],  # the actual KPI documents, newest first
        "loaded_failures": loaded_failures[:25],
        "loaded_total": total_in_window,   # TRUE count in the window
        "fetched": len(recent),            # docs stats were computed over
        "truncated": total_in_window > len(recent),
        "window_applied": window_applied,
        "es_error": es_error,
        "index": settings.jenkins_kpi_index,
        "source": _es_source(),
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
            "source": _es_source()}
