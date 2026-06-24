"""Projects, membership, roles, components and versions."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Short uppercase key used as the issue-key prefix, e.g. "ENG" -> ENG-12.
    key: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_type: Mapped[str] = mapped_column(String(20), default="software", nullable=False)
    avatar_color: Mapped[str] = mapped_column(String(20), default="#2563eb", nullable=False)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Counter for the next issue number in this project (atomic key allocation).
    issue_counter: Mapped[int] = mapped_column(default=0, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Permission scheme governing who can do what in this project. Null => the
    # default scheme is applied (see services.permissions).
    permission_scheme_id: Mapped[int | None] = mapped_column(
        ForeignKey("permission_schemes.id", ondelete="SET NULL"), nullable=True
    )
    external_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)

    lead = relationship("User", foreign_keys=[lead_id])
    permission_scheme = relationship("PermissionScheme", foreign_keys=[permission_scheme_id])
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    components = relationship("Component", back_populates="project", cascade="all, delete-orphan")
    versions = relationship("Version", back_populates="project", cascade="all, delete-orphan")
    issues = relationship("Issue", back_populates="project", cascade="all, delete-orphan")
    boards = relationship("Board", back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base, TimestampMixin):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # admin | member | viewer
    role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)

    project = relationship("Project", back_populates="members")
    user = relationship("User")


class Component(Base, TimestampMixin):
    __tablename__ = "components"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_component_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    project = relationship("Project", back_populates="components")
    lead = relationship("User")


class Version(Base, TimestampMixin):
    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_version_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    released: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    project = relationship("Project", back_populates="versions")
