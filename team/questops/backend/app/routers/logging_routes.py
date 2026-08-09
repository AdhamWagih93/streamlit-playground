"""Logging Health page: per-project/app ELK index insights (size, docs, last
logged, @timestamp health) across the prd + non-prd Elasticsearch connections."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..db import User
from ..integrations import logreport, logstats

router = APIRouter(prefix="/api", tags=["logging"])


@router.get("/logging")
def logging_health(refresh: bool = False, user: User = Depends(current_user)):
    """Loops the inventory projects/apps and reports each app's log-index
    estate from both configured Elasticsearch connections."""
    if refresh:
        logstats.invalidate()
    return logstats.analyze(refresh)


@router.get("/logging/ts-samples")
def logging_ts_samples(index: str, source: str = "prd", good: str = "",
                       good_source: str = "", mode: str = "",
                       user: User = Depends(current_user)):
    """Sample @timestamp + event.original from suspect indices (+ a good
    sibling): the docs that make @timestamp not a proper date, or — with
    mode=future — the docs inside future-dated indices."""
    try:
        return logstats.ts_samples(index, source, good, good_source, mode=mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — surface a clean message, never a 500
        raise HTTPException(502, f"@timestamp sampling failed: {str(exc)[:200]}")


@router.get("/logging/report")
def logging_report(project: str, extra: bool = False, team: str = "",
                   skip_healthy: bool = False, skip_undeployed: bool = False,
                   skip_unmonitored: bool = False,
                   user: User = Depends(current_user)):
    """Comprehensive per-project HTML report (email-ready) for preview.
    Extra envs are excluded unless extra=true; team narrows to the envs that
    team owns; skip_healthy hides score-100 apps."""
    try:
        return logreport.build_report(project, include_extra=extra, team=team or None,
                                      skip_healthy=skip_healthy,
                                      skip_undeployed=skip_undeployed,
                                      skip_unmonitored=skip_unmonitored)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


class ReportSendBody(BaseModel):
    project: str
    recipients: list[str]
    subject: str | None = None
    extra: bool = False
    team: str | None = None
    skip_healthy: bool = False
    skip_undeployed: bool = False
    skip_unmonitored: bool = False


@router.post("/logging/report/send")
def logging_report_send(body: ReportSendBody, user: User = Depends(current_user)):
    """Send the per-project report via the configured SMTP server."""
    try:
        return logreport.send_report(body.project, body.recipients, body.subject,
                                     include_extra=body.extra, team=body.team,
                                     skip_healthy=body.skip_healthy,
                                     skip_undeployed=body.skip_undeployed,
                                     skip_unmonitored=body.skip_unmonitored)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — clean message, never a 500
        raise HTTPException(502, f"send failed: {str(exc)[:600]}")
