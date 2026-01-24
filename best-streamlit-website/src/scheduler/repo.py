from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from src.scheduler.db import get_engine, get_sessionmaker
from src.scheduler.models import Base, SchedulerJob, SchedulerRun


def init_db(database_url: str) -> None:
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)


def list_jobs(database_url: str) -> List[Dict[str, Any]]:
    sm = get_sessionmaker(database_url)
    with sm() as s:
        jobs = s.execute(select(SchedulerJob).order_by(SchedulerJob.created_at.desc())).scalars().all()
        return [j.to_dict() for j in jobs]


def get_job(database_url: str, job_id: str) -> Optional[Dict[str, Any]]:
    sm = get_sessionmaker(database_url)
    with sm() as s:
        j = s.get(SchedulerJob, job_id)
        return j.to_dict() if j else None


def upsert_job(
    database_url: str,
    *,
    job_id: Optional[str],
    enabled: bool,
    label: str,
    server: str,
    tool: str,
    args: Dict[str, Any],
    interval_seconds: int,
) -> Dict[str, Any]:
    sm = get_sessionmaker(database_url)
    with sm() as s:
        if job_id:
            j = s.get(SchedulerJob, job_id)
        else:
            j = None

        if j is None:
            j = SchedulerJob()
            s.add(j)

        j.enabled = bool(enabled)
        j.label = str(label or "Untitled")
        j.server = str(server)
        j.tool = str(tool)
        j.args_json = json.dumps(args or {})
        j.interval_seconds = max(5, int(interval_seconds))

        # If next_run_at isn't set, schedule first run.
        if not j.next_run_at:
            j.next_run_at = datetime.utcnow() + timedelta(seconds=int(j.interval_seconds))

        s.commit()
        return j.to_dict()


def delete_job(database_url: str, job_id: str) -> bool:
    sm = get_sessionmaker(database_url)
    with sm() as s:
        j = s.get(SchedulerJob, job_id)
        if not j:
            return False
        s.delete(j)
        s.commit()
        return True


def claim_due_jobs(database_url: str, *, now: datetime, limit: int) -> List[SchedulerJob]:
    """Return due jobs (best-effort) ordered by next_run_at.

    Note: This is a minimal scheduler; we don't implement distributed locking yet.
    In HA mode, a single scheduler instance should be run.
    """

    sm = get_sessionmaker(database_url)
    with sm() as s:
        q = (
            select(SchedulerJob)
            .where(SchedulerJob.enabled.is_(True))
            .where(SchedulerJob.next_run_at.is_(None) | (SchedulerJob.next_run_at <= now))
            .order_by(SchedulerJob.next_run_at.asc().nullsfirst())
            .limit(int(limit))
        )
        jobs = s.execute(q).scalars().all()

        # Best-effort claim: push next_run_at slightly forward so a second
        # scheduler instance doesn't pick the same job immediately.
        # This is not a true distributed lock.
        claim_until = now + timedelta(seconds=30)
        for j in jobs:
            j.next_run_at = claim_until
        s.commit()

        # Detach for use outside session
        for j in jobs:
            s.expunge(j)
        return list(jobs)


def set_next_run(database_url: str, job_id: str, *, next_run_at: datetime) -> None:
    sm = get_sessionmaker(database_url)
    with sm() as s:
        j = s.get(SchedulerJob, job_id)
        if not j:
            return
        j.next_run_at = next_run_at
        s.commit()


def record_run(
    database_url: str,
    *,
    job_id: str,
    started_at: datetime,
    finished_at: datetime,
    ok: Optional[bool],
    result: Optional[Dict[str, Any]],
    error: Optional[str],
) -> Dict[str, Any]:
    sm = get_sessionmaker(database_url)
    with sm() as s:
        r = SchedulerRun(
            job_id=str(job_id),
            started_at=started_at,
            finished_at=finished_at,
            ok=ok,
            result_json=json.dumps(result) if result is not None else None,
            error=error,
        )
        s.add(r)
        s.commit()
        return r.to_dict()


def list_runs(database_url: str, *, limit: int = 50, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    sm = get_sessionmaker(database_url)
    with sm() as s:
        q = select(SchedulerRun).order_by(SchedulerRun.started_at.desc()).limit(int(limit))
        if job_id:
            q = q.where(SchedulerRun.job_id == str(job_id))
        runs = s.execute(q).scalars().all()
        return [r.to_dict() for r in runs]
