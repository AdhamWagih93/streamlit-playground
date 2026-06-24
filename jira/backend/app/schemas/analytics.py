"""Schemas for project and instance analytics (insights)."""
from __future__ import annotations

from pydantic import BaseModel


class CountItem(BaseModel):
    label: str
    count: int
    color: str | None = None
    category: str | None = None


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
    total_issues: int
    open_issues: int
    in_progress_issues: int
    closed_issues: int
    resolution_rate: float  # closed / total, 0..1
    by_status: list[CountItem]
    by_type: list[CountItem]
    by_priority: list[CountItem]
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


class OverviewStats(BaseModel):
    scope: str  # "all" (instance admin) | "mine" (accessible projects)
    total_projects: int
    total_issues: int
    open_issues: int
    closed_issues: int
    resolution_rate: float
    by_status: list[CountItem]
    by_type: list[CountItem]
    projects: list[ProjectSummary]
