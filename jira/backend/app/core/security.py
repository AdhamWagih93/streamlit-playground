"""Password hashing and JWT token helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except ValueError:
        return False


def _create_token(subject: str | int, minutes: int, token_type: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(subject: str | int, extra: dict | None = None, minutes: int | None = None) -> str:
    return _create_token(subject, minutes or settings.access_token_expire_minutes, "access", extra)


def create_refresh_token(subject: str | int, minutes: int | None = None, extra: dict | None = None) -> str:
    return _create_token(subject, minutes or settings.refresh_token_expire_minutes, "refresh", extra)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT. Raises jwt exceptions on failure."""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
