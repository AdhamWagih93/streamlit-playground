"""Permission scheme routes: schemes, grants, catalog and project assignment."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import PermissionGrant, PermissionScheme, Project, User
from app.schemas.common import Message
from app.schemas.rbac import (
    GrantIn,
    GrantOut,
    PermissionCatalog,
    PermissionDef,
    PermissionSchemeBrief,
    PermissionSchemeIn,
    PermissionSchemeOut,
)
from app.services import permission_keys as P
from app.services.permissions import (
    assert_project_permission,
    is_site_admin,
    require_site_admin,
)

router = APIRouter()


class AssignSchemeIn(BaseModel):
    scheme_id: int | None = None


def _get_scheme(db: Session, scheme_id: int) -> PermissionScheme:
    scheme = db.get(PermissionScheme, scheme_id)
    if scheme is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Permission scheme not found"
        )
    return scheme


# --- Catalog (declared before /{scheme_id} so it isn't shadowed) -----------
@router.get("/catalog", response_model=PermissionCatalog)
def get_catalog(user: User = Depends(get_current_user)) -> PermissionCatalog:
    return PermissionCatalog(
        global_permissions=[
            PermissionDef(key=k, description=v) for k, v in P.GLOBAL_PERMISSIONS.items()
        ],
        project_permissions=[
            PermissionDef(key=k, description=v) for k, v in P.PROJECT_PERMISSIONS.items()
        ],
        holder_types=sorted(P.HOLDER_TYPES),
        special_holders=[
            P.SPECIAL_REPORTER,
            P.SPECIAL_ASSIGNEE,
            P.SPECIAL_PROJECT_LEAD,
            P.SPECIAL_CURRENT_USER,
            P.SPECIAL_ANYONE,
        ],
    )


# --- Schemes ----------------------------------------------------------------
@router.get("", response_model=list[PermissionSchemeBrief])
def list_schemes(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[PermissionScheme]:
    return list(db.scalars(select(PermissionScheme).order_by(PermissionScheme.name)))


@router.post("", response_model=PermissionSchemeBrief, status_code=status.HTTP_201_CREATED)
def create_scheme(
    body: PermissionSchemeIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> PermissionScheme:
    exists = db.scalars(
        select(PermissionScheme).where(PermissionScheme.name == body.name)
    ).first()
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A permission scheme with that name already exists",
        )
    scheme = PermissionScheme(name=body.name, description=body.description)
    db.add(scheme)
    db.commit()
    db.refresh(scheme)
    return scheme


@router.get("/{scheme_id}", response_model=PermissionSchemeOut)
def get_scheme(
    scheme_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PermissionScheme:
    return _get_scheme(db, scheme_id)


@router.patch("/{scheme_id}", response_model=PermissionSchemeBrief)
def update_scheme(
    scheme_id: int,
    body: PermissionSchemeIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> PermissionScheme:
    scheme = _get_scheme(db, scheme_id)
    if body.name != scheme.name:
        dup = db.scalars(
            select(PermissionScheme).where(
                PermissionScheme.name == body.name, PermissionScheme.id != scheme.id
            )
        ).first()
        if dup:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A permission scheme with that name already exists",
            )
        scheme.name = body.name
    scheme.description = body.description
    db.commit()
    db.refresh(scheme)
    return scheme


@router.delete("/{scheme_id}", response_model=Message)
def delete_scheme(
    scheme_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> Message:
    scheme = _get_scheme(db, scheme_id)
    if scheme.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The default permission scheme cannot be deleted",
        )
    db.delete(scheme)
    db.commit()
    return Message(detail="Permission scheme deleted")


# --- Grants -----------------------------------------------------------------
@router.post("/{scheme_id}/grants", response_model=GrantOut, status_code=status.HTTP_201_CREATED)
def add_grant(
    scheme_id: int,
    body: GrantIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> PermissionGrant:
    scheme = _get_scheme(db, scheme_id)
    if body.permission not in P.ALL_PERMISSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown permission: {body.permission}",
        )
    if body.holder_type not in P.HOLDER_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown holder type: {body.holder_type}",
        )
    dup = db.scalars(
        select(PermissionGrant).where(
            PermissionGrant.scheme_id == scheme.id,
            PermissionGrant.permission == body.permission,
            PermissionGrant.holder_type == body.holder_type,
            PermissionGrant.holder_value == body.holder_value,
        )
    ).first()
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That grant already exists in this scheme",
        )
    grant = PermissionGrant(
        scheme_id=scheme.id,
        permission=body.permission,
        holder_type=body.holder_type,
        holder_value=body.holder_value,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return grant


@router.delete("/{scheme_id}/grants/{grant_id}", response_model=Message)
def remove_grant(
    scheme_id: int,
    grant_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> Message:
    scheme = _get_scheme(db, scheme_id)
    grant = db.get(PermissionGrant, grant_id)
    if grant is None or grant.scheme_id != scheme.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found")
    db.delete(grant)
    db.commit()
    return Message(detail="Grant removed")


# --- Project assignment -----------------------------------------------------
@router.put("/projects/{project_id}", response_model=Message)
def assign_scheme_to_project(
    project_id: int,
    body: AssignSchemeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not is_site_admin(db, user):
        assert_project_permission(db, user, project, P.ADMINISTER_PROJECTS)

    if body.scheme_id is not None:
        _get_scheme(db, body.scheme_id)
        project.permission_scheme_id = body.scheme_id
        db.commit()
        return Message(detail="Permission scheme assigned")

    project.permission_scheme_id = None
    db.commit()
    return Message(detail="Permission scheme reset to default")
