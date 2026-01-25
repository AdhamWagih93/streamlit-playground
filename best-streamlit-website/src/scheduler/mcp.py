from __future__ import annotations

import os
from datetime import datetime
from threading import Event, Thread
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from src.scheduler.config import SchedulerConfig
from src.scheduler.repo import (
    delete_job as delete_job_impl,
    get_job as get_job_impl,
    init_db,
    list_jobs as list_jobs_impl,
    list_runs as list_runs_impl,
    set_next_run,
    upsert_job as upsert_job_impl,
)
from src.scheduler.runner import SchedulerRuntimeState, run_scheduler_forever


mcp = FastMCP("scheduler")

_STOP = Event()
_THREAD: Optional[Thread] = None
_STATE = SchedulerRuntimeState(started_at_utc=datetime.utcnow().replace(microsecond=0).isoformat() + "Z")


def _env_bool(name: str, default: bool) -> bool:
    raw = (str((os.environ.get(name) or "")).strip().lower())
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _bootstrap_default_jobs(cfg: SchedulerConfig) -> None:
    """Create a small set of default jobs when the DB is empty.

    Goal: make the scheduler DB non-empty in local dev even before users
    create jobs via the UI.

    Safety:
    - Only auto-runs by default for the repo-local SQLite DB (data/scheduler.db)
    - Can be disabled with SCHEDULER_BOOTSTRAP_JOBS=false
    """

    # Explicit opt-out always wins.
    if not _env_bool("SCHEDULER_BOOTSTRAP_JOBS", True):
        return

    db = str(cfg.database_url or "")
    is_local_sqlite = db.startswith("sqlite:///") and db.replace("\\", "/").endswith("/data/scheduler.db")
    if not is_local_sqlite:
        # Avoid auto-seeding shared/prod databases.
        return

    try:
        existing = list_jobs_impl(cfg.database_url)
        if existing:
            return
    except Exception:
        # If listing fails, don't block startup.
        return

    # Minimal jobs that exercise different servers. These may fail if the
    # target MCP servers are not running; runs are still recorded, which is
    # useful for validating the scheduler loop.
    jobs_to_create = [
        {"label": "Docker: health_check", "server": "docker", "tool": "health_check", "args": {}, "interval_seconds": 60},
        {"label": "Kubernetes: health_check", "server": "kubernetes", "tool": "health_check", "args": {}, "interval_seconds": 60},
        {"label": "Jenkins: get_server_info", "server": "jenkins", "tool": "get_server_info", "args": {}, "interval_seconds": 60},
        {"label": "Nexus: nexus_health_check", "server": "nexus", "tool": "nexus_health_check", "args": {}, "interval_seconds": 60},
    ]

    now = datetime.utcnow()
    for spec in jobs_to_create:
        try:
            job = upsert_job_impl(
                cfg.database_url,
                job_id=None,
                enabled=True,
                label=str(spec["label"]),
                server=str(spec["server"]),
                tool=str(spec["tool"]),
                args=dict(spec.get("args") or {}),
                interval_seconds=int(spec.get("interval_seconds") or 60),
            )

            # Make the first run happen quickly.
            job_id = str(job.get("id") or "")
            if job_id:
                set_next_run(cfg.database_url, job_id, next_run_at=now)
        except Exception:
            # Best-effort only.
            continue


def start_background_scheduler(cfg: SchedulerConfig) -> None:
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return

    init_db(cfg.database_url)
    _bootstrap_default_jobs(cfg)

    _THREAD = Thread(
        target=run_scheduler_forever,
        args=(cfg, _STOP, _STATE),
        name="scheduler-loop",
        daemon=True,
    )
    _THREAD.start()


@mcp.tool
def scheduler_health() -> Dict[str, Any]:
    cfg = SchedulerConfig.from_env()
    alive = bool(_THREAD and _THREAD.is_alive())
    return {
        "ok": True,
        "service": "scheduler",
        "thread_alive": alive,
        "tick_seconds": int(cfg.tick_seconds),
        "db": cfg.database_url.split(":", 1)[0],
        "started_at_utc": _STATE.started_at_utc,
        "last_tick_at_utc": _STATE.last_tick_at_utc,
        "last_tick_summary": _STATE.last_tick_summary,
    }


@mcp.tool
def scheduler_list_jobs() -> Dict[str, Any]:
    cfg = SchedulerConfig.from_env()
    jobs = list_jobs_impl(cfg.database_url)
    return {"ok": True, "jobs": jobs}


@mcp.tool
def scheduler_get_job(job_id: str) -> Dict[str, Any]:
    cfg = SchedulerConfig.from_env()
    job = get_job_impl(cfg.database_url, str(job_id))
    if not job:
        return {"ok": False, "error": "not_found"}
    return {"ok": True, "job": job}


@mcp.tool
def scheduler_upsert_job(
    *,
    job_id: Optional[str] = None,
    enabled: bool = True,
    label: str,
    server: str,
    tool: str,
    args: Optional[Dict[str, Any]] = None,
    interval_seconds: int = 60,
) -> Dict[str, Any]:
    cfg = SchedulerConfig.from_env()
    job = upsert_job_impl(
        cfg.database_url,
        job_id=str(job_id) if job_id else None,
        enabled=bool(enabled),
        label=str(label),
        server=str(server),
        tool=str(tool),
        args=dict(args or {}),
        interval_seconds=int(interval_seconds),
    )
    return {"ok": True, "job": job}


@mcp.tool
def scheduler_delete_job(job_id: str) -> Dict[str, Any]:
    cfg = SchedulerConfig.from_env()
    ok = delete_job_impl(cfg.database_url, str(job_id))
    return {"ok": bool(ok)}


@mcp.tool
def scheduler_list_runs(limit: int = 50, job_id: Optional[str] = None) -> Dict[str, Any]:
    cfg = SchedulerConfig.from_env()
    runs = list_runs_impl(cfg.database_url, limit=int(limit), job_id=str(job_id) if job_id else None)
    return {"ok": True, "runs": runs}


def run() -> None:
    cfg = SchedulerConfig.from_env()
    start_background_scheduler(cfg)
    # HTTP-only MCP server; stdio mode is no longer supported.
    # Use explicit host/port when supported.
    # FastMCP signature differs across versions, so keep it permissive.
    try:
        mcp.run(transport="http", host=cfg.mcp_host, port=int(cfg.mcp_port))
    except TypeError:
        mcp.run(transport="http")


if __name__ == "__main__":
    run()
