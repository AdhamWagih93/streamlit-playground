"""Project role definition and per-project role-actor routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Project, ProjectRole, ProjectRoleActor, User
from app.schemas.common import Message
from app.schemas.rbac import ProjectRoleIn, ProjectRoleOut, RoleActorIn, RoleActorOut
from app.services import permission_keys as P
from app.services.permissions import (
    assert_project_permission,
    has_project_permission,
    is_site_admin,
    require_site_admin,
)

router = APIRouter()

_DEFAULT_ROLE_NAMES = {"Administrators", "Developers", "Viewers"}


def _get_role(db: Session, role_id: int) -> ProjectRole:
    role = db.get(ProjectRole, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return role


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


# --- Global role definitions ----------------------------------------------
@router.get("", response_model=list[ProjectRoleOut])
def list_roles(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProjectRole]:
    return list(db.scalars(select(ProjectRole).order_by(ProjectRole.name)))


@router.post("", response_model=ProjectRoleOut, status_code=status.HTTP_201_CREATED)
def create_role(
    body: ProjectRoleIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> ProjectRole:
    exists = db.scalars(select(ProjectRole).where(ProjectRole.name == body.name)).first()
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A role with that name already exists"
        )
    role = ProjectRole(name=body.name, description=body.description)
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


@router.patch("/{role_id}", response_model=ProjectRoleOut)
def update_role(
    role_id: int,
    body: ProjectRoleIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> ProjectRole:
    role = _get_role(db, role_id)
    if body.name != role.name:
        dup = db.scalars(
            select(ProjectRole).where(ProjectRole.name == body.name, ProjectRole.id != role.id)
        ).first()
        if dup:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A role with that name already exists",
            )
        role.name = body.name
    role.description = body.description
    db.commit()
    db.refresh(role)
    return role


@router.delete("/{role_id}", response_model=Message)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> Message:
    role = _get_role(db, role_id)
    if role.name in _DEFAULT_ROLE_NAMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Default roles cannot be deleted",
        )
    db.delete(role)
    db.commit()
    return Message(detail="Role deleted")


# --- Per-project role actors ------------------------------------------------
@router.get("/projects/{project_id}/actors", response_model=list[RoleActorOut])
def list_role_actors(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProjectRoleActor]:
    project = _get_project(db, project_id)
    if not (is_site_admin(db, user) or has_project_permission(db, user, project, P.BROWSE_PROJECTS)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission required to browse this project",
        )
    return list(
        db.scalars(
            select(ProjectRoleActor).where(ProjectRoleActor.project_id == project.id)
        )
    )


@router.post(
    "/projects/{project_id}/actors",
    response_model=RoleActorOut,
    status_code=status.HTTP_201_CREATED,
)
def add_role_actor(
    project_id: int,
    body: RoleActorIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectRoleActor:
    project = _get_project(db, project_id)
    if not is_site_admin(db, user):
        assert_project_permission(db, user, project, P.ADMINISTER_PROJECTS)

    if (body.user_id is None) == (body.group_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Exactly one of user_id or group_id must be set",
        )

    role = _get_role(db, body.role_id)

    dup = db.scalars(
        select(ProjectRoleActor).where(
            ProjectRoleActor.project_id == project.id,
            ProjectRoleActor.role_id == role.id,
            ProjectRoleActor.user_id == body.user_id,
            ProjectRoleActor.group_id == body.group_id,
        )
    ).first()
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That actor is already assigned to the role",
        )

    actor = ProjectRoleActor(
        project_id=project.id,
        role_id=role.id,
        user_id=body.user_id,
        group_id=body.group_id,
    )
    db.add(actor)
    db.commit()
    db.refresh(actor)
    return actor


@router.delete("/projects/{project_id}/actors/{actor_id}", response_model=Message)
def remove_role_actor(
    project_id: int,
    actor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    project = _get_project(db, project_id)
    if not is_site_admin(db, user):
        assert_project_permission(db, user, project, P.ADMINISTER_PROJECTS)

    actor = db.get(ProjectRoleActor, actor_id)
    if actor is None or actor.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role actor not found")
    db.delete(actor)
    db.commit()
    return Message(detail="Role actor removed")
