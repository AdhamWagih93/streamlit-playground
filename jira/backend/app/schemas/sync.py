"""Schemas for the per-project Jira sync."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class SyncRunOut(ORMModel):
    id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    trigger: str
    processed: int
    created: int
    updated: int
    errors: int
    message: str | None = None


class SyncLinkOut(ORMModel):
    id: int
    project_id: int
    connection_id: int
    jira_project_key: str
    jira_project_id: str | None = None
    enabled: bool
    status: str
    updated_watermark: datetime | None = None
    cursor_start_at: int
    total_issues: int
    processed_issues: int
    last_synced_at: datetime | None = None
    last_error: str | None = None
    sync_permissions: bool


class SyncLinkDetail(SyncLinkOut):
    recent_runs: list[SyncRunOut] = []


class LinkProjectIn(BaseModel):
    # Which configured Jira connection to use; defaults to the default one.
    connection_id: int | None = None
    # Override the Jira key to match; defaults to the Trackly project key.
    jira_project_key: str | None = None
    sync_permissions: bool = True


class DiscoverResult(BaseModel):
    found: bool
    jira_project_key: str | None = None
    name: str | None = None
    jira_project_id: str | None = None
    issue_count: int | None = None
    message: str | None = None


class SyncActionResult(BaseModel):
    status: str
    message: str
    link: SyncLinkOut | None = None
