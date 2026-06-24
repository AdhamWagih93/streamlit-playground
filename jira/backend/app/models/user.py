"""User accounts."""
from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # is_admin == site/instance administrator (the highest-privilege role).
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # How this account authenticates: local | ldap | entra. Directory accounts
    # cannot log in with a local password.
    auth_source: Mapped[str] = mapped_column(String(20), default="local", nullable=False)
    # DN (LDAP) or object id (Entra) of the external identity, when applicable.
    external_directory_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Stable external id used by the migration tool to map a Jira accountId.
    external_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)

    reported_issues = relationship(
        "Issue", back_populates="reporter", foreign_keys="Issue.reporter_id"
    )
    assigned_issues = relationship(
        "Issue", back_populates="assignee", foreign_keys="Issue.assignee_id"
    )
