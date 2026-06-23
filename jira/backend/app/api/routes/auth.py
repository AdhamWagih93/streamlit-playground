"""Authentication routes: login, register, token refresh and self-profile."""
from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import User
from app.schemas.auth import RefreshRequest, RegisterRequest, Token
from app.schemas.user import UserOut, UserUpdate

router = APIRouter()

_CRED_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
    headers={"WWW-Authenticate": "Bearer"},
)


@router.post("/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> Token:
    """OAuth2 password flow. ``form.username`` is treated as the user's email."""
    user = db.scalars(select(User).where(User.email == form.username)).first()
    if user is None or not verify_password(form.password, user.password_hash):
        raise _CRED_EXC
    if not user.is_active:
        raise _CRED_EXC
    return Token(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
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
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/refresh", response_model=Token)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> Token:
    try:
        data = decode_token(payload.refresh_token)
        if data.get("type") != "refresh":
            raise _CRED_EXC
        user_id = int(data["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _CRED_EXC
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _CRED_EXC
    return Token(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserOut)
def read_me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Self-service profile update. Privilege fields are intentionally ignored."""
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url
    if payload.timezone is not None:
        user.timezone = payload.timezone
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
