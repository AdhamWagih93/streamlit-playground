"""Per-project Jira sync routes.

Mounted at ``/sync`` under the API prefix. Access is restricted to site
administrators or users holding ``ADMINISTER_PROJECTS`` on the target project.
Sync execution is kicked off via FastAPI ``BackgroundTasks`` so the request
returns immediately while the resumable engine runs in the background.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Project, ProjectSyncLink, SyncRun, User
from app.models.identity import JiraConnection
from app.schemas.common import Message
from app.schemas.sync import (
    DiscoverResult,
    LinkProjectIn,
    SyncActionResult,
    SyncLinkDetail,
    SyncLinkOut,
    SyncRunOut,
)
from app.services import permission_keys as P
from app.services.jira_sync import (
    discover,
    get_default_connection,
    start_sync_background,
)
from app.services.permissions import has_project_permission, is_site_admin

router = APIRouter()


@router.get("/connections")
def list_sync_connections(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    """Non-secret list of enabled Jira connections, for the link picker.

    Available to any authenticated user (project admins need it to link a
    project) — secrets are never included.
    """
    rows = db.scalars(
        select(JiraConnection).where(JiraConnection.enabled.is_(True)).order_by(
            JiraConnection.is_default.desc(), JiraConnection.id.asc()
        )
    )
    return [
        {"id": c.id, "name": c.name, "base_url": c.base_url, "is_default": c.is_default}
        for c in rows
    ]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _authz(db: Session, user: User, project: Project) -> None:
    """Raise 403 unless the user is a site admin or project administrator."""
    if is_site_admin(db, user):
        return
    if has_project_permission(db, user, project, P.ADMINISTER_PROJECTS):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Project administration privileges required",
    )


def _get_link(db: Session, project_id: int) -> ProjectSyncLink | None:
    return db.scalars(
        select(ProjectSyncLink).where(ProjectSyncLink.project_id == project_id)
    ).first()


def _resolve_connection(db: Session, connection_id: int | None) -> JiraConnection:
    if connection_id is not None:
        conn = db.get(JiraConnection, connection_id)
        if conn is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Jira connection not found")
        return conn
    conn = get_default_connection(db)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No enabled Jira connection configured",
        )
    return conn


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}", response_model=SyncLinkDetail)
def get_sync_link(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncLinkDetail:
    project = _get_project(db, project_id)
    _authz(db, user, project)
    link = _get_link(db, project_id)
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project is not linked to Jira")
    recent = db.scalars(
        select(SyncRun).where(SyncRun.link_id == link.id).order_by(SyncRun.id.desc()).limit(10)
    ).all()
    detail = SyncLinkDetail.model_validate(link)
    detail.recent_runs = [SyncRunOut.model_validate(r) for r in recent]
    return detail


@router.get("/projects/{project_id}/discover", response_model=DiscoverResult)
def discover_project(
    project_id: int,
    connection_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiscoverResult:
    project = _get_project(db, project_id)
    _authz(db, user, project)
    connection = _resolve_connection(db, connection_id)
    return DiscoverResult(**discover(db, project, connection))


@router.post("/projects/{project_id}/link", response_model=SyncLinkOut)
def link_project(
    project_id: int,
    body: LinkProjectIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncLinkOut:
    project = _get_project(db, project_id)
    _authz(db, user, project)
    connection = _resolve_connection(db, body.connection_id)
    jira_key = (body.jira_project_key or project.key).strip().upper()

    probe = discover(db, project, connection)
    if not probe.get("found"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=probe.get("message") or f"No matching Jira project for key {jira_key}",
        )
    # Honour the actual key Jira returned (canonical case).
    jira_key = probe.get("jira_project_key") or jira_key

    link = _get_link(db, project_id)
    if link is None:
        link = ProjectSyncLink(
            project_id=project.id,
            connection_id=connection.id,
            jira_project_key=jira_key,
            jira_project_id=probe.get("jira_project_id"),
            enabled=True,
            status="idle",
            sync_permissions=body.sync_permissions,
        )
        db.add(link)
    else:
        link.connection_id = connection.id
        link.jira_project_key = jira_key
        link.jira_project_id = probe.get("jira_project_id")
        link.sync_permissions = body.sync_permissions
        if link.status in ("error",):
            link.status = "idle"
    db.commit()
    db.refresh(link)
    return SyncLinkOut.model_validate(link)


@router.post("/projects/{project_id}/start", response_model=SyncActionResult)
def start_sync(
    project_id: int,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncActionResult:
    project = _get_project(db, project_id)
    _authz(db, user, project)
    link = _get_link(db, project_id)
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project is not linked to Jira; create a link first",
        )
    if link.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A sync is already running")

    link.status = "running"
    link.last_error = None
    db.commit()

    background.add_task(start_sync_background, project.id, "manual", user.id)
    return SyncActionResult(
        status="accepted",
        message="Sync started",
        link=SyncLinkOut.model_validate(link),
    )


@router.post("/projects/{project_id}/pause", response_model=SyncActionResult)
def pause_sync(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncActionResult:
    project = _get_project(db, project_id)
    _authz(db, user, project)
    link = _get_link(db, project_id)
    if link is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project is not linked to Jira")
    # The engine observes this flag between batches and stops gracefully.
    link.status = "paused"
    db.commit()
    db.refresh(link)
    return SyncActionResult(
        status="paused",
        message="Pause requested; the sync will stop after the current batch",
        link=SyncLinkOut.model_validate(link),
    )


@router.post("/projects/{project_id}/resume", response_model=SyncActionResult)
def resume_sync(
    project_id: int,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncActionResult:
    project = _get_project(db, project_id)
    _authz(db, user, project)
    link = _get_link(db, project_id)
    if link is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project is not linked to Jira")
    if link.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A sync is already running")

    # Resume keeps the persisted watermark + cursor untouched.
    link.status = "running"
    link.last_error = None
    db.commit()

    background.add_task(start_sync_background, project.id, "resume", user.id)
    return SyncActionResult(
        status="accepted",
        message="Sync resumed",
        link=SyncLinkOut.model_validate(link),
    )


@router.delete("/projects/{project_id}/link", response_model=Message)
def unlink_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Message:
    project = _get_project(db, project_id)
    _authz(db, user, project)
    link = _get_link(db, project_id)
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project is not linked to Jira")
    db.delete(link)
    db.commit()
    return Message(detail="Sync link removed")


@router.get("/projects/{project_id}/runs", response_model=list[SyncRunOut])
def list_runs(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SyncRunOut]:
    project = _get_project(db, project_id)
    _authz(db, user, project)
    link = _get_link(db, project_id)
    if link is None:
        return []
    runs = db.scalars(
        select(SyncRun).where(SyncRun.link_id == link.id).order_by(SyncRun.id.desc())
    ).all()
    return [SyncRunOut.model_validate(r) for r in runs]
