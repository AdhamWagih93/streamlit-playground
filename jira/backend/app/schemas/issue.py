"""Issue schemas: the core read/write contract for issues and their children."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, field_validator

from app.schemas.common import ORMModel
from app.schemas.meta import IssueTypeOut, PriorityOut, StatusOut
from app.schemas.project import ComponentOut, ProjectBrief, VersionOut
from app.schemas.user import UserBrief


# --- Comments --------------------------------------------------------------
class CommentOut(ORMModel):
    id: int
    body: str
    author: UserBrief | None = None
    created_at: datetime
    updated_at: datetime


class CommentIn(BaseModel):
    body: str


# --- Worklogs --------------------------------------------------------------
class WorklogOut(ORMModel):
    id: int
    time_spent_seconds: int
    comment: str | None = None
    started_at: datetime
    author: UserBrief | None = None


class WorklogIn(BaseModel):
    # Accept either a human string ('2h 30m') or raw seconds.
    time_spent: str | None = None
    time_spent_seconds: int | None = None
    comment: str | None = None
    started_at: datetime | None = None


# --- Attachments -----------------------------------------------------------
class AttachmentOut(ORMModel):
    id: int
    filename: str
    content_type: str
    size_bytes: int
    author: UserBrief | None = None
    created_at: datetime


# --- History ---------------------------------------------------------------
class HistoryOut(ORMModel):
    id: int
    field: str
    old_value: str | None = None
    new_value: str | None = None
    author: UserBrief | None = None
    created_at: datetime


# --- Links -----------------------------------------------------------------
class IssueRef(ORMModel):
    id: int
    key: str
    summary: str
    status: StatusOut | None = None
    type: IssueTypeOut | None = None


class IssueLinkOut(BaseModel):
    id: int
    link_type: str
    issue: IssueRef


class IssueLinkIn(BaseModel):
    link_type: str
    target_key: str


# --- Issue read models -----------------------------------------------------
class IssueListItem(ORMModel):
    id: int
    key: str
    summary: str
    type: IssueTypeOut | None = None
    status: StatusOut | None = None
    priority: PriorityOut | None = None
    assignee: UserBrief | None = None
    reporter: UserBrief | None = None
    story_points: float | None = None
    parent_id: int | None = None
    epic_id: int | None = None
    sprint_id: int | None = None
    rank: str = "n"
    due_date: date | None = None
    updated_at: datetime
    labels: list[str] = []

    @field_validator("labels", mode="before")
    @classmethod
    def _labels_to_names(cls, v):
        # Accept ORM Label objects (from from_attributes) or plain strings.
        if isinstance(v, (list, tuple, set)):
            return [getattr(x, "name", x) for x in v]
        return v


class IssueDetail(IssueListItem):
    project: ProjectBrief | None = None
    description: str | None = None
    original_estimate_seconds: int | None = None
    remaining_estimate_seconds: int | None = None
    resolution: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    components: list[ComponentOut] = []
    fix_versions: list[VersionOut] = []
    comments: list[CommentOut] = []
    attachments: list[AttachmentOut] = []
    worklogs: list[WorklogOut] = []
    subtasks: list[IssueRef] = []
    links: list[IssueLinkOut] = []


# --- Issue write models ----------------------------------------------------
class IssueCreate(BaseModel):
    project_id: int
    type_id: int
    summary: str
    description: str | None = None
    status_id: int | None = None
    priority_id: int | None = None
    assignee_id: int | None = None
    reporter_id: int | None = None
    parent_id: int | None = None
    epic_id: int | None = None
    sprint_id: int | None = None
    story_points: float | None = None
    due_date: date | None = None
    label_names: list[str] = []
    component_ids: list[int] = []
    fix_version_ids: list[int] = []


class IssueUpdate(BaseModel):
    type_id: int | None = None
    summary: str | None = None
    description: str | None = None
    status_id: int | None = None
    priority_id: int | None = None
    assignee_id: int | None = None
    reporter_id: int | None = None
    parent_id: int | None = None
    epic_id: int | None = None
    sprint_id: int | None = None
    story_points: float | None = None
    original_estimate: str | None = None
    remaining_estimate: str | None = None
    resolution: str | None = None
    due_date: date | None = None
    label_names: list[str] | None = None
    component_ids: list[int] | None = None
    fix_version_ids: list[int] | None = None


class IssueRankUpdate(BaseModel):
    # Place the issue immediately after `after_id` (or at top if null) and
    # optionally move it into a sprint/backlog.
    after_id: int | None = None
    before_id: int | None = None
    sprint_id: int | None = None
    status_id: int | None = None
