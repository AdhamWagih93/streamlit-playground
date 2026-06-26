"""Project routes: projects, membership, components and versions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user
from app.core.database import get_db
from app.models import (
    Board,
    Component,
    Project,
    ProjectMember,
    User,
    Version,
)
from app.services import permission_keys as P
from app.services.permissions import (
    assert_project_permission,
    has_project_permission,
    is_site_admin,
    visible_project_ids,
)
from app.schemas.common import Message
from app.schemas.project import (
    ComponentIn,
    ComponentOut,
    MemberIn,
    MemberOut,
    ProjectBrief,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    VersionIn,
    VersionOut,
)

router = APIRouter()


def _resolve_project(db: Session, key_or_id: str | int) -> Project:
    """Return a Project by integer id or by key (case-insensitive). 404 if missing."""
    project: Project | None = None
    key_str = str(key_or_id)
    if key_str.isdigit():
        project = db.get(Project, int(key_str))
    if project is None:
        project = db.scalars(
            select(Project).where(Project.key == key_str.upper())
        ).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return project


def _require_project_admin(db: Session, user: User, project: Project) -> None:
    """Allow site admins or holders of ADMINISTER_PROJECTS on this project."""
    if is_site_admin(db, user) or has_project_permission(db, user, project, P.ADMINISTER_PROJECTS):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Project administrator privileges required",
    )


# --- Projects --------------------------------------------------------------
@router.get("", response_model=list[ProjectBrief])
def list_projects(
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Project]:
    stmt = select(Project)
    if not include_archived:
        stmt = stmt.where(Project.is_archived.is_(False))
    vis = visible_project_ids(db, user)
    if vis is not None:
        stmt = stmt.where(Project.id.in_(vis or {-1}))
    stmt = stmt.order_by(Project.key.asc())
    return list(db.scalars(stmt))


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Project:
    key = payload.key.upper()
    existing = db.scalars(select(Project).where(Project.key == key)).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A project with that key already exists",
        )
    project = Project(
        key=key,
        name=payload.name,
        description=payload.description,
        project_type=payload.project_type,
        avatar_color=payload.avatar_color,
        lead_id=payload.lead_id,
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=admin.id, role="admin"))
    db.add(
        Board(
            project_id=project.id,
            name=f"{project.name} board",
            board_type="scrum",
        )
    )
    db.flush()
    # Apply the default permission scheme and make the creator a project admin
    # so the RBAC engine grants them full control of the new project.
    from app.services.permissions import setup_project_defaults

    setup_project_defaults(db, project, admin)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{key_or_id}", response_model=ProjectOut)
def get_project(
    key_or_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    project = _resolve_project(db, key_or_id)
    assert_project_permission(db, user, project, P.BROWSE_PROJECTS)
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    _require_project_admin(db, user, project)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(project, field, value)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", response_model=Message)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Message:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    db.delete(project)
    db.commit()
    return Message(detail="Project deleted")


# --- Members ---------------------------------------------------------------
@router.get("/{project_id}/members", response_model=list[MemberOut])
def list_members(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProjectMember]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    assert_project_permission(db, user, project, P.BROWSE_PROJECTS)
    return list(
        db.scalars(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )
    )


@router.post(
    "/{project_id}/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
)
def add_member(
    project_id: int,
    payload: MemberIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectMember:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    _require_project_admin(db, user, project)
    if db.get(User, payload.user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    member = db.scalars(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == payload.user_id,
        )
    ).first()
    if member is not None:
        # Upsert role for an existing membership.
        member.role = payload.role
    else:
        member = ProjectMember(
            project_id=project_id, user_id=payload.user_id, role=payload.role
        )
        db.add(member)
    db.commit()
    db.refresh(member)
    return member


@router.delete("/{project_id}/members/{user_id}", response_model=Message)
def remove_member(
    project_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    _require_project_admin(db, user, project)
    member = db.scalars(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    ).first()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )
    db.delete(member)
    db.commit()
    return Message(detail="Member removed")


# --- Components ------------------------------------------------------------
@router.get("/{project_id}/components", response_model=list[ComponentOut])
def list_components(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Component]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    assert_project_permission(db, user, project, P.BROWSE_PROJECTS)
    return list(
        db.scalars(select(Component).where(Component.project_id == project_id))
    )


@router.post(
    "/{project_id}/components",
    response_model=ComponentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_component(
    project_id: int,
    payload: ComponentIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Component:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    _require_project_admin(db, user, project)
    component = Component(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        lead_id=payload.lead_id,
    )
    db.add(component)
    db.commit()
    db.refresh(component)
    return component


@router.patch(
    "/{project_id}/components/{cid}", response_model=ComponentOut
)
def update_component(
    project_id: int,
    cid: int,
    payload: ComponentIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Component:
    _require_project_admin(db, user, _resolve_project(db, project_id))
    component = db.scalars(
        select(Component).where(
            Component.id == cid, Component.project_id == project_id
        )
    ).first()
    if component is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Component not found"
        )
    component.name = payload.name
    component.description = payload.description
    component.lead_id = payload.lead_id
    db.add(component)
    db.commit()
    db.refresh(component)
    return component


@router.delete(
    "/{project_id}/components/{cid}", response_model=Message
)
def delete_component(
    project_id: int,
    cid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    _require_project_admin(db, user, _resolve_project(db, project_id))
    component = db.scalars(
        select(Component).where(
            Component.id == cid, Component.project_id == project_id
        )
    ).first()
    if component is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Component not found"
        )
    db.delete(component)
    db.commit()
    return Message(detail="Component deleted")


# --- Versions --------------------------------------------------------------
@router.get("/{project_id}/versions", response_model=list[VersionOut])
def list_versions(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Version]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    assert_project_permission(db, user, project, P.BROWSE_PROJECTS)
    return list(
        db.scalars(select(Version).where(Version.project_id == project_id))
    )


@router.post(
    "/{project_id}/versions",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    project_id: int,
    payload: VersionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Version:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    _require_project_admin(db, user, project)
    version = Version(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        released=payload.released,
        archived=payload.archived,
        release_date=payload.release_date,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


@router.patch("/{project_id}/versions/{vid}", response_model=VersionOut)
def update_version(
    project_id: int,
    vid: int,
    payload: VersionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Version:
    _require_project_admin(db, user, _resolve_project(db, project_id))
    version = db.scalars(
        select(Version).where(
            Version.id == vid, Version.project_id == project_id
        )
    ).first()
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Version not found"
        )
    version.name = payload.name
    version.description = payload.description
    version.released = payload.released
    version.archived = payload.archived
    version.release_date = payload.release_date
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


@router.delete("/{project_id}/versions/{vid}", response_model=Message)
def delete_version(
    project_id: int,
    vid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    _require_project_admin(db, user, _resolve_project(db, project_id))
    version = db.scalars(
        select(Version).where(
            Version.id == vid, Version.project_id == project_id
        )
    ).first()
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Version not found"
        )
    db.delete(version)
    db.commit()
    return Message(detail="Version deleted")
