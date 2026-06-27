"""Insights/analytics routes.

- ``/analytics/projects/{key_or_id}`` — detailed stats for one project, visible
  to anyone with BROWSE_PROJECTS on it (or a site admin).
- ``/analytics/my`` — an overview across the projects the caller can browse.
- ``/analytics/overview`` — instance-wide overview, site administrators only.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Project, User
from app.schemas.analytics import OverviewStats, ProjectStats, Window
from app.services import analytics
from app.services import permission_keys as P
from app.services.permissions import (
    has_project_permission,
    is_site_admin,
    require_site_admin,
    visible_project_ids,
)

router = APIRouter()

_PERIOD_RE = re.compile(r"^(\d+)([dwmy])$")
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}


def _parse_date(value: str) -> datetime:
    """Parse an ISO date or datetime into a tz-aware (UTC) datetime."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value!r} (use YYYY-MM-DD)")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _window(
    period: str = Query("all", description="Time window: 'all' or e.g. 7d / 4w / 3m / 1y"),
    frm: str | None = Query(None, alias="from", description="Start date (YYYY-MM-DD), overrides period"),
    to: str | None = Query(None, description="End date (YYYY-MM-DD, inclusive), overrides period"),
) -> tuple[datetime | None, datetime | None, Window]:
    """Resolve the insights time window from query params. Used as a dependency."""
    now = datetime.now(timezone.utc)
    if frm or to:
        start = _parse_date(frm) if frm else None
        # 'to' is inclusive of the whole day.
        end = (_parse_date(to) + timedelta(days=1)) if to else None
        return start, end, Window(period="custom", start=start, end=end)
    period = (period or "all").lower()
    if period == "all":
        return None, None, Window(period="all")
    m = _PERIOD_RE.match(period)
    if not m:
        raise HTTPException(status_code=400, detail=f"Invalid period: {period!r} (use 'all' or e.g. 30d, 4w, 3m, 1y)")
    start = now - timedelta(days=int(m.group(1)) * _UNIT_DAYS[m.group(2)])
    return start, now, Window(period=period, start=start, end=now)


def _resolve_project(db: Session, key_or_id: str) -> Project:
    project = None
    if key_or_id.isdigit():
        project = db.get(Project, int(key_or_id))
    if project is None:
        project = db.scalars(select(Project).where(Project.key == key_or_id.upper())).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.get("/overview", response_model=OverviewStats)
def overview(
    win=Depends(_window),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> OverviewStats:
    """Instance-wide insights across every project (site admin only)."""
    start, end, window = win
    return analytics.overview_stats(db, None, scope="all", start=start, end=end, window=window)


@router.get("/my", response_model=OverviewStats)
def my_overview(
    win=Depends(_window),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OverviewStats:
    """Insights across the projects the current user can browse."""
    start, end, window = win
    ids = visible_project_ids(db, user)  # None => site admin (all projects)
    return analytics.overview_stats(
        db, None if ids is None else list(ids), scope="mine", start=start, end=end, window=window
    )


@router.get("/projects/{key_or_id}", response_model=ProjectStats)
def project_insights(
    key_or_id: str,
    win=Depends(_window),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectStats:
    project = _resolve_project(db, key_or_id)
    if not (is_site_admin(db, user) or has_project_permission(db, user, project, P.BROWSE_PROJECTS)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this project's insights",
        )
    start, end, window = win
    return analytics.project_stats(db, project, start=start, end=end, window=window)
