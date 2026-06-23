"""Issue types, statuses, priorities, the Issue entity, labels and links."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Column,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

# --- Many-to-many association tables --------------------------------------
issue_labels = Table(
    "issue_labels",
    Base.metadata,
    Column("issue_id", ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True),
    Column("label_id", ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True),
)

issue_components = Table(
    "issue_components",
    Base.metadata,
    Column("issue_id", ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True),
    Column("component_id", ForeignKey("components.id", ondelete="CASCADE"), primary_key=True),
)

issue_fix_versions = Table(
    "issue_fix_versions",
    Base.metadata,
    Column("issue_id", ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True),
    Column("version_id", ForeignKey("versions.id", ondelete="CASCADE"), primary_key=True),
)


class IssueType(Base):
    __tablename__ = "issue_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    icon: Mapped[str] = mapped_column(String(40), default="task", nullable=False)
    color: Mapped[str] = mapped_column(String(20), default="#6b7280", nullable=False)
    is_subtask: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Null project_id => global/default type available to every project.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)


class StatusCategory:
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class Status(Base):
    __tablename__ = "statuses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    # todo | in_progress | done
    category: Mapped[str] = mapped_column(String(20), default="todo", nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)


class Priority(Base):
    __tablename__ = "priorities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    icon: Mapped[str] = mapped_column(String(40), default="medium", nullable=False)
    color: Mapped[str] = mapped_column(String(20), default="#f59e0b", nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=3, nullable=False)


class Label(Base):
    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)


class Issue(Base, TimestampMixin):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human-readable key, e.g. "ENG-42". Unique and immutable once assigned.
    key: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # per-project sequence
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("issue_types.id"), nullable=False)
    status_id: Mapped[int] = mapped_column(ForeignKey("statuses.id"), nullable=False, index=True)
    priority_id: Mapped[int | None] = mapped_column(ForeignKey("priorities.id"), nullable=True)

    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    # Hierarchy: parent_id links sub-tasks; epic_id links any issue to its epic.
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), nullable=True)
    epic_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), nullable=True)

    story_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_estimate_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remaining_estimate_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(60), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sprints.id", ondelete="SET NULL"), nullable=True, index=True)
    # Lexicographic rank string for stable board/backlog ordering.
    rank: Mapped[str] = mapped_column(String(64), default="n", nullable=False, index=True)

    external_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)

    # Relationships
    project = relationship("Project", back_populates="issues")
    type = relationship("IssueType")
    status = relationship("Status")
    priority = relationship("Priority")
    reporter = relationship("User", back_populates="reported_issues", foreign_keys=[reporter_id])
    assignee = relationship("User", back_populates="assigned_issues", foreign_keys=[assignee_id])
    parent = relationship("Issue", remote_side=[id], foreign_keys=[parent_id], backref="subtasks")
    epic = relationship("Issue", remote_side=[id], foreign_keys=[epic_id])
    sprint = relationship("Sprint", back_populates="issues")

    labels = relationship("Label", secondary=issue_labels, lazy="selectin")
    components = relationship("Component", secondary=issue_components, lazy="selectin")
    fix_versions = relationship("Version", secondary=issue_fix_versions, lazy="selectin")

    comments = relationship("Comment", back_populates="issue", cascade="all, delete-orphan", order_by="Comment.created_at")
    attachments = relationship("Attachment", back_populates="issue", cascade="all, delete-orphan")
    worklogs = relationship("Worklog", back_populates="issue", cascade="all, delete-orphan")
    history = relationship("IssueHistory", back_populates="issue", cascade="all, delete-orphan", order_by="IssueHistory.created_at")


class IssueLink(Base, TimestampMixin):
    """Directed link between two issues (blocks, relates to, duplicates, ...)."""

    __tablename__ = "issue_links"
    __table_args__ = (UniqueConstraint("source_id", "target_id", "link_type", name="uq_issue_link"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    target_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    # blocks | is_blocked_by | relates_to | duplicates | is_duplicated_by | clones | is_cloned_by
    link_type: Mapped[str] = mapped_column(String(40), default="relates_to", nullable=False)

    source = relationship("Issue", foreign_keys=[source_id], backref="outward_links")
    target = relationship("Issue", foreign_keys=[target_id], backref="inward_links")
