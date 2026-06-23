"""Custom fields, their per-issue values, and saved search filters."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class CustomField(Base):
    __tablename__ = "custom_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # text | number | date | select | multiselect | user | checkbox | url
    field_type: Mapped[str] = mapped_column(String(30), default="text", nullable=False)
    # JSON-encoded option list for select/multiselect types.
    options_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)


class CustomFieldValue(Base):
    __tablename__ = "custom_field_values"

    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("custom_fields.id", ondelete="CASCADE"), nullable=False, index=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded

    field = relationship("CustomField")


class SavedFilter(Base, TimestampMixin):
    __tablename__ = "saved_filters"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Trackly Query Language string (see app/services/tql.py).
    query: Mapped[str] = mapped_column(Text, nullable=False)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    owner = relationship("User")
