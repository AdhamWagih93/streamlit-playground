"""Resumable, per-project Jira synchronisation state."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class ProjectSyncLink(Base, TimestampMixin):
    """Links a Trackly project to a Jira project (matched by key) and holds the
    resumable sync cursor so an interrupted sync continues where it stopped.
    """

    __tablename__ = "project_sync_links"
    __table_args__ = (UniqueConstraint("project_id", name="uq_sync_link_project"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("jira_connections.id", ondelete="CASCADE"), nullable=False)
    jira_project_key: Mapped[str] = mapped_column(String(40), nullable=False)
    jira_project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # idle | running | paused | error | completed
    status: Mapped[str] = mapped_column(String(20), default="idle", nullable=False)
    # Resume cursor: only re-pull issues updated at/after this watermark, and
    # within the current page start position. Together they make sync resumable.
    updated_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor_start_at: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    total_issues: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_issues: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whether to also import the Jira project's permission scheme on sync.
    sync_permissions: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    connection = relationship("JiraConnection")
    runs = relationship("SyncRun", back_populates="link", cascade="all, delete-orphan", order_by="SyncRun.id.desc()")


class SyncRun(Base):
    """Audit record for a single sync execution."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("project_sync_links.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # running | completed | paused | error
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)  # manual | auto | resume
    processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    link = relationship("ProjectSyncLink", back_populates="runs")
