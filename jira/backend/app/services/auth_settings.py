"""Helpers for the instance authentication policy (AuthSettings singleton)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AuthSettings


def get_auth_settings(db: Session) -> AuthSettings:
    """Return the singleton AuthSettings row, creating defaults on first use."""
    row = db.get(AuthSettings, 1)
    if row is None:
        row = AuthSettings(
            id=1,
            allow_local_login=True,
            allow_self_registration=True,
            access_token_minutes=settings.access_token_expire_minutes,
            refresh_token_minutes=settings.refresh_token_expire_minutes,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def access_token_minutes(db: Session) -> int:
    return get_auth_settings(db).access_token_minutes or settings.access_token_expire_minutes


def refresh_token_minutes(db: Session) -> int:
    return get_auth_settings(db).refresh_token_minutes or settings.refresh_token_expire_minutes


def local_login_allowed(db: Session) -> bool:
    return get_auth_settings(db).allow_local_login


def self_registration_allowed(db: Session) -> bool:
    return get_auth_settings(db).allow_self_registration


def registration_email_allowed(db: Session, email: str) -> bool:
    """Check the email against the optional self-registration domain allowlist."""
    row = get_auth_settings(db)
    raw = (row.registration_allowed_domains or "").strip()
    if not raw:
        return True
    domains = {d.strip().lower().lstrip("@") for d in raw.replace("\n", ",").split(",") if d.strip()}
    if not domains:
        return True
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in domains
