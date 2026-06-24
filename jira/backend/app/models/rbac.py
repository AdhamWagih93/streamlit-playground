"""Role-based access control: groups, project roles, permission schemes.

The model mirrors Jira's permission architecture so schemes import cleanly:
- Groups (optionally directory-synced) collect users.
- Project roles are global *definitions*; role actors bind users/groups to a
  role within a single project.
- A permission scheme is a reusable set of grants (permission -> holder).
- A project points at one permission scheme.
- Global permission grants govern instance-wide rights (e.g. ADMINISTER).
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Table, Column, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

# Users <-> Groups (many-to-many)
user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)


class Group(Base, TimestampMixin):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When set, membership is managed by a directory (ldap/entra) sync, not by
    # hand. Stored so the UI can show "synced from <source>" and avoid edits.
    directory_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    members = relationship("User", secondary=user_groups, backref="groups", lazy="selectin")


class ProjectRole(Base, TimestampMixin):
    """A global role definition (Administrators, Developers, Viewers, ...)."""

    __tablename__ = "project_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ProjectRoleActor(Base):
    """Binds a user or group to a project role within a specific project."""

    __tablename__ = "project_role_actors"
    __table_args__ = (
        UniqueConstraint("project_id", "role_id", "user_id", "group_id", name="uq_role_actor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("project_roles.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=True)

    role = relationship("ProjectRole")
    user = relationship("User")
    group = relationship("Group")


class PermissionScheme(Base, TimestampMixin):
    __tablename__ = "permission_schemes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # External id of the originating Jira permission scheme (for re-sync).
    external_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)

    grants = relationship("PermissionGrant", back_populates="scheme", cascade="all, delete-orphan", lazy="selectin")


class PermissionGrant(Base):
    """One permission granted to one holder within a scheme."""

    __tablename__ = "permission_grants"
    __table_args__ = (
        UniqueConstraint("scheme_id", "permission", "holder_type", "holder_value", name="uq_permission_grant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scheme_id: Mapped[int] = mapped_column(ForeignKey("permission_schemes.id", ondelete="CASCADE"), nullable=False, index=True)
    permission: Mapped[str] = mapped_column(String(60), nullable=False)
    # group | user | role | special  (see services.permission_keys)
    holder_type: Mapped[str] = mapped_column(String(20), nullable=False)
    holder_value: Mapped[str | None] = mapped_column(String(255), nullable=True)

    scheme = relationship("PermissionScheme", back_populates="grants")


class GlobalPermissionGrant(Base):
    """An instance-wide permission granted to a group or user."""

    __tablename__ = "global_permission_grants"
    __table_args__ = (
        UniqueConstraint("permission", "holder_type", "holder_value", name="uq_global_grant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    permission: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    holder_type: Mapped[str] = mapped_column(String(20), nullable=False)  # group | user
    holder_value: Mapped[str] = mapped_column(String(255), nullable=False)
