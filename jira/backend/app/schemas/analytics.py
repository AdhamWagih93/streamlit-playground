"""Schemas for project and instance analytics (insights)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class Window(BaseModel):
    """The time window applied to the (descriptive) insights, by issue creation.

    ``period`` is the requested shortcut ("all", "30d", "custom", ...); start/end
    are the resolved bounds (end exclusive). "Needs attention" signals are always
    current-state and ignore this window.
    """

    period: str = "all"
    start: datetime | None = None
    end: datetime | None = None


class CountItem(BaseModel):
    label: str
    count: int
    color: str | None = None
    category: str | None = None


# --- "Needs attention" signals --------------------------------------------
class AttentionIssue(BaseModel):
    key: str
    summary: str
    priority: str | None = None
    priority_color: str | None = None
    assignee: str | None = None
    status: str | None = None
    due_date: date | None = None
    days_overdue: int | None = None
    updated_at: datetime | None = None


class AttentionItem(BaseModel):
    key: str          # overdue | high_priority | blocked | unassigned | stale | open_bugs | stale_wip
    label: str
    description: str
    count: int
    severity: str     # high | medium | low
    tql: str | None = None       # a query that lists exactly these issues
    samples: list[AttentionIssue] = []


class SprintHealth(BaseModel):
    sprint_id: int
    name: str
    goal: str | None = None
    end_date: datetime | None = None
    days_remaining: int | None = None
    total_points: float
    completed_points: float
    percent_complete: float       # 0..1
    incomplete_issues: int
    at_risk: bool
    risk_reason: str | None = None


class VelocityPoint(BaseModel):
    sprint_id: int
    sprint_name: str
    committed_points: float
    completed_points: float
    completed_issues: int


class ProjectStats(BaseModel):
    project_id: int
    project_key: str
    project_name: str
    window: Window = Window()
    total_issues: int
    open_issues: int
    in_progress_issues: int
    closed_issues: int
    resolution_rate: float  # closed / total, 0..1
    # Action-first: what needs attention now, highest-severity first.
    attention: list[AttentionItem]
    attention_score: int
    sprint_health: SprintHealth | None = None
    by_status: list[CountItem]
    by_type: list[CountItem]
    by_priority: list[CountItem]
    by_component: list[CountItem] = []
    velocity: list[VelocityPoint]
    avg_velocity_points: float
    avg_velocity_issues: float


class ProjectSummary(BaseModel):
    project_id: int
    project_key: str
    project_name: str
    avatar_color: str
    total_issues: int
    open_issues: int
    closed_issues: int
    resolution_rate: float
    avg_velocity_points: float
    # Attention signals for ranking/guidance.
    attention_score: int = 0
    overdue: int = 0
    high_priority_open: int = 0
    unassigned_open: int = 0
    blocked: int = 0
    at_risk_sprint: bool = False
    needs_attention: bool = False
    top_reasons: list[str] = []


class OverviewStats(BaseModel):
    scope: str  # "all" (instance admin) | "mine" (accessible projects)
    window: Window = Window()
    total_projects: int
    total_issues: int
    open_issues: int
    closed_issues: int
    resolution_rate: float
    # Instance-wide attention roll-up.
    total_overdue: int = 0
    total_unassigned_open: int = 0
    total_high_priority_open: int = 0
    total_blocked: int = 0
    projects_at_risk: int = 0
    projects_needing_attention: int = 0
    top_attention: list[AttentionIssue] = []
    by_status: list[CountItem]
    by_type: list[CountItem]
    # Sorted by attention_score desc — most-urgent projects first.
    projects: list[ProjectSummary]
