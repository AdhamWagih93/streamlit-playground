"""Boards and sprints (agile planning)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class Board(Base, TimestampMixin):
    __tablename__ = "boards"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # scrum | kanban
    board_type: Mapped[str] = mapped_column(String(20), default="scrum", nullable=False)

    project = relationship("Project", back_populates="boards")
    sprints = relationship("Sprint", back_populates="board", cascade="all, delete-orphan")


class Sprint(Base, TimestampMixin):
    __tablename__ = "sprints"

    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("boards.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    # future | active | closed
    state: Mapped[str] = mapped_column(String(20), default="future", nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    complete_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    board = relationship("Board", back_populates="sprints")
    issues = relationship("Issue", back_populates="sprint")
