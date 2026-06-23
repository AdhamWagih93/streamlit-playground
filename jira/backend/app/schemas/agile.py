"""Board and sprint schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel
from app.schemas.issue import IssueListItem


class SprintOut(ORMModel):
    id: int
    board_id: int
    name: str
    goal: str | None = None
    state: str
    start_date: datetime | None = None
    end_date: datetime | None = None
    complete_date: datetime | None = None


class SprintIn(BaseModel):
    name: str
    goal: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


class SprintUpdate(BaseModel):
    name: str | None = None
    goal: str | None = None
    state: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


class BoardOut(ORMModel):
    id: int
    project_id: int
    name: str
    board_type: str


class BoardIn(BaseModel):
    project_id: int
    name: str
    board_type: str = "scrum"


class BoardColumn(BaseModel):
    status_id: int
    status_name: str
    category: str
    issues: list[IssueListItem]


class BoardView(BaseModel):
    board: BoardOut
    columns: list[BoardColumn]
    active_sprint: SprintOut | None = None


class BacklogView(BaseModel):
    board: BoardOut
    sprints: list[SprintOut]
    # issues grouped by sprint id; key 0 == backlog (no sprint)
    sprint_issues: dict[int, list[IssueListItem]]
    backlog: list[IssueListItem]
