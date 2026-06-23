"""Jira -> Trackly data migration package.

Public entry points:
    - ``MigrationConfig`` (config.py): env-driven settings.
    - ``JiraClient`` (jira_client.py): REST client.
    - ``Importer`` (importer.py): orchestrates the upsert into Postgres.
    - ``run`` (here): convenience wrapper used by the CLI.
"""
from __future__ import annotations

from app.migration.config import MigrationConfig
from app.migration.importer import ImportOptions, Importer, ImportStats
from app.migration.jira_client import JiraClient

__all__ = [
    "MigrationConfig",
    "JiraClient",
    "Importer",
    "ImportOptions",
    "ImportStats",
    "run",
    "build_client",
]


def build_client(config: MigrationConfig) -> JiraClient:
    """Construct a :class:`JiraClient` from a :class:`MigrationConfig`."""
    config.validate()
    return JiraClient(
        base_url=config.base_url,
        email=config.email,
        api_token=config.api_token,
        verify=config.verify_ssl,
        server_token=config.is_server,
    )


def run(config: MigrationConfig | None = None,
        project_keys: list[str] | None = None,
        jql_extra: str = "") -> ImportStats:
    """Run a full migration using *config* (defaults to env)."""
    from app.core.database import SessionLocal

    config = config or MigrationConfig.from_env()
    client = build_client(config)
    keys = project_keys if project_keys is not None else (config.project_keys or None)
    extra = jql_extra or config.jql
    try:
        with SessionLocal() as db:
            importer = Importer(db, client)
            return importer.run(project_keys=keys, jql_extra=extra)
    finally:
        client.close()
