"""Logging Health page: per-project/app ELK index insights (size, docs, last
logged, @timestamp health) across the prd + non-prd Elasticsearch connections."""

from fastapi import APIRouter, Depends

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
