"""Logging Health page: per-project/app ELK index insights (size, docs, last
logged, @timestamp health) across the prd + non-prd Elasticsearch connections."""

from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..db import User
from ..integrations import logstats

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
                       good_source: str = "", user: User = Depends(current_user)):
    """Sample @timestamp values from a suspect index (+ a good sibling) to show
    exactly which documents make @timestamp not a proper date."""
    try:
        return logstats.ts_samples(index, source, good, good_source)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — surface a clean message, never a 500
        raise HTTPException(502, f"@timestamp sampling failed: {str(exc)[:200]}")
