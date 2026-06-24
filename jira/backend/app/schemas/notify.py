"""Schemas for per-user notification preferences."""
from __future__ import annotations

from pydantic import BaseModel


class NotificationEventDef(BaseModel):
    event: str
    label: str


class PreferenceRow(BaseModel):
    event: str
    label: str
    in_app: bool
    email: bool


class NotificationPreferences(BaseModel):
    # Whether outbound email is enabled instance-wide (so the UI can show a hint).
    email_available: bool
    rows: list[PreferenceRow]


class PreferenceUpdate(BaseModel):
    event: str
    channel: str  # in_app | email
    enabled: bool


class PreferencesUpdate(BaseModel):
    updates: list[PreferenceUpdate]
