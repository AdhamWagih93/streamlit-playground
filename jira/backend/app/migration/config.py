"""Migration configuration read from environment variables.

All Jira-connection and import-scoping settings live here so the rest of the
migration package never touches ``os.environ`` directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _as_bool(raw: str | None, default: bool = True) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


@dataclass
class MigrationConfig:
    """Connection + scope settings for a Jira -> Trackly migration run."""

    base_url: str = ""
    email: str = ""
    api_token: str = ""
    # cloud  -> HTTP Basic (email, api_token)
    # server -> Bearer api_token (Personal Access Token); email ignored
    auth_mode: str = "cloud"
    project_keys: list[str] = field(default_factory=list)
    jql: str = ""
    verify_ssl: bool = True

    @classmethod
    def from_env(cls) -> "MigrationConfig":
        return cls(
            base_url=os.environ.get("JIRA_BASE_URL", "").strip().rstrip("/"),
            email=os.environ.get("JIRA_EMAIL", "").strip(),
            api_token=os.environ.get("JIRA_API_TOKEN", "").strip(),
            auth_mode=os.environ.get("JIRA_AUTH_MODE", "cloud").strip().lower() or "cloud",
            project_keys=_split(os.environ.get("JIRA_PROJECT_KEYS")),
            jql=os.environ.get("JIRA_JQL", "").strip(),
            verify_ssl=_as_bool(os.environ.get("JIRA_VERIFY_SSL"), True),
        )

    @property
    def is_server(self) -> bool:
        return self.auth_mode == "server"

    def validate(self) -> None:
        """Raise ``ValueError`` if mandatory settings are missing."""
        problems: list[str] = []
        if not self.base_url:
            problems.append("JIRA_BASE_URL is required")
        if not self.api_token:
            problems.append("JIRA_API_TOKEN is required")
        if not self.is_server and not self.email:
            problems.append("JIRA_EMAIL is required for cloud auth (set JIRA_AUTH_MODE=server for PAT)")
        if problems:
            raise ValueError("; ".join(problems))
