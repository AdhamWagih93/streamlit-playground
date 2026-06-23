"""Metadata schemas: issue types, statuses, priorities, labels, custom fields."""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ORMModel


class IssueTypeOut(ORMModel):
    id: int
    name: str
    icon: str
    color: str
    is_subtask: bool
    project_id: int | None = None


class IssueTypeIn(BaseModel):
    name: str
    icon: str = "task"
    color: str = "#6b7280"
    is_subtask: bool = False
    project_id: int | None = None


class StatusOut(ORMModel):
    id: int
    name: str
    category: str
    order: int
    project_id: int | None = None


class StatusIn(BaseModel):
    name: str
    category: str = "todo"
    order: int = 0
    project_id: int | None = None


class PriorityOut(ORMModel):
    id: int
    name: str
    icon: str
    color: str
    rank: int


class LabelOut(ORMModel):
    id: int
    name: str


class CustomFieldOut(ORMModel):
    id: int
    name: str
    field_type: str
    options_json: str | None = None
    project_id: int | None = None


class CustomFieldIn(BaseModel):
    name: str
    field_type: str = "text"
    options_json: str | None = None
    project_id: int | None = None
