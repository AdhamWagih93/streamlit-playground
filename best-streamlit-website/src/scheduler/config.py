from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SchedulerConfig:
    """Runtime configuration for the scheduler service.

    DB selection:
    - PLATFORM_DATABASE_URL: shared DB URL (preferred)
    - SCHEDULER_DATABASE_URL: scheduler-specific DB URL
    - If neither is set, defaults to local SQLite at data/scheduler.db

    Loop:
    - SCHEDULER_TICK_SECONDS: how often to check for due jobs (default: 5)
    - SCHEDULER_MAX_JOBS_PER_TICK: cap due jobs executed per tick (default: 20)

    MCP server:
    - SCHEDULER_MCP_TRANSPORT: stdio|http|sse (default: http)
    - SCHEDULER_MCP_HOST (default: 0.0.0.0)
    - SCHEDULER_MCP_PORT (default: 8010)

    Notes:
    - In Kubernetes, set PLATFORM_DATABASE_URL to the central Postgres cluster.
    - In local Windows runs, omit DB URLs to use SQLite.
    """

    database_url: str
    tick_seconds: int
    max_jobs_per_tick: int

    mcp_transport: str
    mcp_host: str
    mcp_port: int

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        db_url = (
            os.environ.get("PLATFORM_DATABASE_URL")
            or os.environ.get("SCHEDULER_DATABASE_URL")
            or ""
        ).strip()

        if not db_url:
            # Default sqlite path under repo-root data/.
            repo_root = Path(__file__).resolve().parents[2]
            data_dir = repo_root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_url = f"sqlite:///{(data_dir / 'scheduler.db').as_posix()}"

        tick_seconds = _env_int("SCHEDULER_TICK_SECONDS", 5)
        max_jobs = _env_int("SCHEDULER_MAX_JOBS_PER_TICK", 20)

        transport = (os.environ.get("SCHEDULER_MCP_TRANSPORT") or "http").lower().strip()
        host = (os.environ.get("SCHEDULER_MCP_HOST") or "0.0.0.0").strip()
        port = _env_int("SCHEDULER_MCP_PORT", 8010)

        return cls(
            database_url=db_url,
            tick_seconds=max(1, int(tick_seconds)),
            max_jobs_per_tick=max(1, int(max_jobs)),
            mcp_transport=transport,
            mcp_host=host,
            mcp_port=int(port),
        )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return int(default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)
