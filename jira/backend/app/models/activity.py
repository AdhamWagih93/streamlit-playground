"""Comments, attachments, worklogs, change history and notifications."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class Comment(Base, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)

    issue = relationship("Issue", back_populates="comments")
    author = relationship("User")


class Attachment(Base, TimestampMixin):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)  # path under attachments_dir

    issue = relationship("Issue", back_populates="attachments")
    author = relationship("User")


class Worklog(Base, TimestampMixin):
    __tablename__ = "worklogs"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    time_spent_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    issue = relationship("Issue", back_populates="worklogs")
    author = relationship("User")


class IssueHistory(Base):
    """A single field change recorded against an issue (audit trail)."""

    __tablename__ = "issue_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    field: Mapped[str] = mapped_column(String(80), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    issue = relationship("Issue", back_populates="history")
    author = relationship("User")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    verb: Mapped[str] = mapped_column(String(60), nullable=False)  # assigned | commented | mentioned | ...
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user = relationship("User", foreign_keys=[user_id])
    actor = relationship("User", foreign_keys=[actor_id])
