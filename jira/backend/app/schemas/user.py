"""User schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.schemas.common import ORMModel


class UserBrief(ORMModel):
    id: int
    username: str
    display_name: str
    # Plain str on output: stored emails may use internal/reserved domains
    # (e.g. migrated users get synthesized "<id>@imported.local" addresses),
    # which strict EmailStr validation would reject. Input schemas still validate.
    email: str
    avatar_url: str | None = None


class UserOut(UserBrief):
    timezone: str = "UTC"
    is_active: bool = True
    is_admin: bool = False
    created_at: datetime | None = None


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    display_name: str
    password: str
    is_admin: bool = False
    timezone: str = "UTC"


class UserUpdate(BaseModel):
    display_name: str | None = None
    avatar_url: str | None = None
    timezone: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None
    password: str | None = None
