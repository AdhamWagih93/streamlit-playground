from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base


Base = declarative_base()


def _utcnow() -> datetime:
    return datetime.utcnow()


class SchedulerJob(Base):
    __tablename__ = "scheduler_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enabled = Column(Boolean, default=True, nullable=False)

    label = Column(String(256), nullable=False)
    server = Column(String(64), nullable=False)
    tool = Column(String(128), nullable=False)
    args_json = Column(Text, default="{}", nullable=False)

    interval_seconds = Column(Integer, default=60, nullable=False)
    next_run_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "enabled": bool(self.enabled),
            "label": self.label,
            "server": self.server,
            "tool": self.tool,
            "args_json": self.args_json,
            "interval_seconds": int(self.interval_seconds),
            "next_run_at": self.next_run_at.isoformat() + "Z" if self.next_run_at else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }


class SchedulerRun(Base):
    __tablename__ = "scheduler_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String(36), nullable=False, index=True)

    started_at = Column(DateTime, default=_utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)

    ok = Column(Boolean, nullable=True)
    result_json = Column(Text, nullable=True)
    error = Column(Text, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "finished_at": self.finished_at.isoformat() + "Z" if self.finished_at else None,
            "ok": self.ok,
            "result": _safe_json_loads(self.result_json) if self.result_json else None,
            "error": self.error,
        }


def _safe_json_loads(raw: Optional[str]) -> Optional[Any]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None
