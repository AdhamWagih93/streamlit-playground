"""Insights/analytics routes.

- ``/analytics/projects/{key_or_id}`` — detailed stats for one project, visible
  to anyone with BROWSE_PROJECTS on it (or a site admin).
- ``/analytics/my`` — an overview across the projects the caller can browse.
- ``/analytics/overview`` — instance-wide overview, site administrators only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Project, User
from app.schemas.analytics import OverviewStats, ProjectStats
from app.services import analytics
from app.services import permission_keys as P
from app.services.permissions import (
    has_project_permission,
    is_site_admin,
    require_site_admin,
    visible_project_ids,
)

router = APIRouter()


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
    db: Session = Depends(get_db), _admin: User = Depends(require_site_admin)
) -> OverviewStats:
    """Instance-wide insights across every project (site admin only)."""
    return analytics.overview_stats(db, None, scope="all")


@router.get("/my", response_model=OverviewStats)
def my_overview(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> OverviewStats:
    """Insights across the projects the current user can browse."""
    ids = visible_project_ids(db, user)  # None => site admin (all projects)
    return analytics.overview_stats(db, None if ids is None else list(ids), scope="mine")


@router.get("/projects/{key_or_id}", response_model=ProjectStats)
def project_insights(
    key_or_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ProjectStats:
    project = _resolve_project(db, key_or_id)
    if not (is_site_admin(db, user) or has_project_permission(db, user, project, P.BROWSE_PROJECTS)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this project's insights",
        )
    return analytics.project_stats(db, project)
