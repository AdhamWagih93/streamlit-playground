"""Authentication request/response schemas."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    display_name: str
    password: str
