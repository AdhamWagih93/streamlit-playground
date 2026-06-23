"""User management routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user
from app.core.database import get_db
from app.core.security import hash_password
from app.models import User
from app.schemas.common import Message
from app.schemas.user import UserCreate, UserOut, UserUpdate

router = APIRouter()


@router.get("", response_model=list[UserOut])
def list_users(
    q: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[User]:
    stmt = select(User)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                User.username.ilike(like),
                User.email.ilike(like),
                User.display_name.ilike(like),
            )
        )
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    stmt = stmt.order_by(User.display_name)
    return list(db.scalars(stmt).all())


@router.get("/{user_id}", response_model=UserOut)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> User:
    existing = db.scalars(
        select(User).where(
            or_(User.email == payload.email, User.username == payload.username)
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that email or username already exists",
        )
    user = User(
        username=payload.username,
        email=payload.email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
        timezone=payload.timezone,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> User:
    if not current.is_admin and current.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only modify your own account",
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url
    if payload.timezone is not None:
        user.timezone = payload.timezone
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    # Privilege fields are admin-only.
    if current.is_admin:
        if payload.is_active is not None:
            user.is_active = payload.is_active
        if payload.is_admin is not None:
            user.is_admin = payload.is_admin

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", response_model=Message)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> Message:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = False
    db.add(user)
    db.commit()
    return Message(detail="User deactivated")
