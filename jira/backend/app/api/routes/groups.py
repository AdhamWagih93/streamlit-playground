"""Group management routes.

Reads are open to site admins or holders of the global BROWSE_USERS permission;
writes are restricted to site administrators.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Group, User, user_groups
from app.schemas.common import Message
from app.schemas.rbac import GroupBrief, GroupIn, GroupMemberIn, GroupOut
from app.services import permission_keys as P
from app.services.permissions import (
    has_global_permission,
    is_site_admin,
    require_site_admin,
)

router = APIRouter()


def require_browse_users(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> User:
    """Allow site admins or holders of the global BROWSE_USERS permission."""
    if is_site_admin(db, user) or has_global_permission(db, user, P.BROWSE_USERS):
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Permission required to browse users and groups",
    )


def _get_group(db: Session, group_id: int) -> Group:
    group = db.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.get("", response_model=list[GroupBrief])
def list_groups(
    q: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_browse_users),
) -> list[Group]:
    stmt = select(Group)
    if q:
        stmt = stmt.where(Group.name.ilike(f"%{q}%"))
    stmt = stmt.order_by(Group.name)
    return list(db.scalars(stmt))


@router.get("/{group_id}", response_model=GroupOut)
def get_group(
    group_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_browse_users),
) -> Group:
    return _get_group(db, group_id)


@router.post("", response_model=GroupBrief, status_code=status.HTTP_201_CREATED)
def create_group(
    body: GroupIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> Group:
    exists = db.scalars(select(Group).where(Group.name == body.name)).first()
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A group with that name already exists"
        )
    group = Group(name=body.name, description=body.description)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.patch("/{group_id}", response_model=GroupBrief)
def update_group(
    group_id: int,
    body: GroupIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> Group:
    group = _get_group(db, group_id)
    if body.name != group.name:
        if group.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="System groups cannot be renamed",
            )
        dup = db.scalars(
            select(Group).where(Group.name == body.name, Group.id != group.id)
        ).first()
        if dup:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A group with that name already exists",
            )
        group.name = body.name
    group.description = body.description
    db.commit()
    db.refresh(group)
    return group


@router.delete("/{group_id}", response_model=Message)
def delete_group(
    group_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> Message:
    group = _get_group(db, group_id)
    if group.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="System groups cannot be deleted"
        )
    db.delete(group)
    db.commit()
    return Message(detail="Group deleted")


@router.post("/{group_id}/members", response_model=GroupOut)
def add_group_member(
    group_id: int,
    body: GroupMemberIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> Group:
    group = _get_group(db, group_id)
    member = db.get(User, body.user_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    present = db.scalar(
        select(user_groups.c.user_id).where(
            user_groups.c.group_id == group.id, user_groups.c.user_id == body.user_id
        )
    )
    if present is None:
        db.execute(user_groups.insert().values(group_id=group.id, user_id=body.user_id))
        db.commit()
    db.refresh(group)
    return group


@router.delete("/{group_id}/members/{user_id}", response_model=Message)
def remove_group_member(
    group_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_site_admin),
) -> Message:
    group = _get_group(db, group_id)
    db.execute(
        user_groups.delete().where(
            user_groups.c.group_id == group.id, user_groups.c.user_id == user_id
        )
    )
    db.commit()
    return Message(detail="Member removed")
