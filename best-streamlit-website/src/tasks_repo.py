"""Task repository abstraction using SQLAlchemy.

Default storage: SQLite (data/tasks.db).
To switch to PostgreSQL set environment variable DATABASE_URL, e.g.:
  export DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname

All list-like fields (tags, comments, history) are stored as JSON text.
This keeps the schema simple and portable. For production you may wish
to normalize comments/history into dedicated tables.
"""
from __future__ import annotations

import os
import json
from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy import (
    create_engine, Column, String, Text, Float, select
)
from sqlalchemy.orm import sessionmaker, declarative_base


Base = declarative_base()


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True)
    title = Column(String(512), nullable=False)
    description = Column(Text, default="")
    assignee = Column(String(128), nullable=True)
    reporter = Column(String(128), nullable=True)
    reviewer = Column(String(128), nullable=True)
    priority = Column(String(32), default="Medium")
    status = Column(String(64), default="Backlog", index=True)
    created_at = Column(String(64), nullable=False)
    due_date = Column(String(64), nullable=True)
    estimates_hours = Column(Float, default=0.0)
    tags = Column(Text, default="[]")  # JSON list
    comments = Column(Text, default="[]")  # JSON list of dicts
    history = Column(Text, default="[]")  # JSON list of dicts
    checklist = Column(Text, default="[]")  # JSON list of {id,text,done,created_at}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "assignee": self.assignee,
            "reporter": self.reporter,
            "reviewer": self.reviewer,
            "priority": self.priority,
            "status": self.status,
            "created_at": self.created_at,
            "due_date": self.due_date,
            "estimates_hours": self.estimates_hours,
            "tags": json.loads(self.tags or "[]"),
            "comments": json.loads(self.comments or "[]"),
            "history": json.loads(self.history or "[]"),
            "checklist": json.loads(self.checklist or "[]"),
        }


_engine = None
SessionLocal = None


def get_engine():
    global _engine, SessionLocal
    if _engine is not None:
        return _engine
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # default sqlite path relative to project root data folder
        base_dir = os.path.dirname(os.path.dirname(__file__))
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "tasks.db")
        db_url = f"sqlite:///{db_path}"
    _engine = create_engine(db_url, future=True, echo=False)
    SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
    return _engine


def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
    # lightweight migration: ensure checklist column exists (SQLite / Postgres)
    with engine.connect() as conn:
        try:
            if engine.url.get_backend_name().startswith('sqlite'):
                cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(tasks)").fetchall()]
                if 'checklist' not in cols:
                    conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN checklist TEXT DEFAULT '[]'")
                if 'reporter' not in cols:
                    conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN reporter TEXT")
                if 'reviewer' not in cols:
                    conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN reviewer TEXT")
            else:
                # generic check for non-sqlite
                res = conn.exec_driver_sql("SELECT column_name FROM information_schema.columns WHERE table_name='tasks'")
                cols = [r[0] for r in res]
                if 'checklist' not in cols:
                    conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN checklist TEXT")
                if 'reporter' not in cols:
                    conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN reporter TEXT")
                if 'reviewer' not in cols:
                    conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN reviewer TEXT")
        except Exception:
            pass


def _session():
    if SessionLocal is None:
        get_engine()
    return SessionLocal()


def get_all_tasks() -> List[Dict[str, Any]]:
    with _session() as s:
        tasks = s.execute(select(Task)).scalars().all()
        return [t.to_dict() for t in tasks]


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    with _session() as s:
        t = s.get(Task, task_id)
        return t.to_dict() if t else None


def create_task(task_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Insert task_dict (expects already formed id & history)."""
    with _session() as s:
        t = Task(
            id=task_dict["id"],
            title=task_dict.get("title", "Untitled"),
            description=task_dict.get("description", ""),
            assignee=task_dict.get("assignee"),
            reporter=task_dict.get("reporter"),
            reviewer=task_dict.get("reviewer"),
            priority=task_dict.get("priority", "Medium"),
            status=task_dict.get("status", "Backlog"),
            created_at=task_dict.get("created_at", datetime.utcnow().isoformat()),
            due_date=task_dict.get("due_date"),
            estimates_hours=task_dict.get("estimates_hours", 0.0),
            tags=json.dumps(task_dict.get("tags", [])),
            comments=json.dumps(task_dict.get("comments", [])),
            history=json.dumps(task_dict.get("history", [])),
            checklist=json.dumps(task_dict.get("checklist", [])),
        )
        s.add(t)
        s.commit()
        return t.to_dict()


def update_task(task_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _session() as s:
        t = s.get(Task, task_dict["id"])
        if not t:
            return None
        t.title = task_dict.get("title", t.title)
        t.description = task_dict.get("description", t.description)
        t.assignee = task_dict.get("assignee", t.assignee)
        # New fields
        t.reporter = task_dict.get("reporter", t.reporter)
        t.reviewer = task_dict.get("reviewer", t.reviewer)
        # Standard attributes
        t.priority = task_dict.get("priority", t.priority)
        t.status = task_dict.get("status", t.status)
        t.due_date = task_dict.get("due_date", t.due_date)
        t.estimates_hours = task_dict.get("estimates_hours", t.estimates_hours)
        if "tags" in task_dict:
            t.tags = json.dumps(task_dict.get("tags") or [])
        if "comments" in task_dict:
            t.comments = json.dumps(task_dict.get("comments") or [])
        if "history" in task_dict:
            t.history = json.dumps(task_dict.get("history") or [])
        if "checklist" in task_dict:
            t.checklist = json.dumps(task_dict.get("checklist") or [])
        s.commit()
        return t.to_dict()


def append_history(task_id: str, what: str, by: str = "system"):
    with _session() as s:
        t = s.get(Task, task_id)
        if not t:
            return
        history = json.loads(t.history or "[]")
        history.append({"when": datetime.utcnow().isoformat(), "what": what, "by": by})
        t.history = json.dumps(history)
        s.commit()


def update_task_status(task_id: str, new_status: str, by: str = "user"):
    with _session() as s:
        t = s.get(Task, task_id)
        if not t:
            return
        t.status = new_status
        history = json.loads(t.history or "[]")
        history.append({"when": datetime.utcnow().isoformat(), "what": f"status->{new_status}", "by": by})
        t.history = json.dumps(history)
        s.commit()


def add_comment(task_id: str, text: str, by: str = "You"):
    with _session() as s:
        t = s.get(Task, task_id)
        if not t:
            return
        comments = json.loads(t.comments or "[]")
        comments.insert(0, {"when": datetime.utcnow().isoformat(), "by": by, "text": text})
        t.comments = json.dumps(comments)
        history = json.loads(t.history or "[]")
        history.append({"when": datetime.utcnow().isoformat(), "what": "comment_added", "by": by})
        t.history = json.dumps(history)
        s.commit()


def delete_task(task_id: str):
    with _session() as s:
        t = s.get(Task, task_id)
        if not t:
            return
        s.delete(t)
        s.commit()


# ---------------- Checklist helpers ----------------
import uuid as _uuid


def add_check_item(task_id: str, text: str, by: str = "You"):
    if not text.strip():
        return
    with _session() as s:
        t = s.get(Task, task_id)
        if not t:
            return
        checklist = json.loads(t.checklist or "[]")
        item = {"id": str(_uuid.uuid4()), "text": text.strip(), "done": False, "created_at": datetime.utcnow().isoformat()}
        checklist.append(item)
        t.checklist = json.dumps(checklist)
        history = json.loads(t.history or "[]")
        history.append({"when": datetime.utcnow().isoformat(), "what": "check_added", "by": by})
        t.history = json.dumps(history)
        s.commit()


def toggle_check_item(task_id: str, item_id: str, done: bool, by: str = "You"):
    with _session() as s:
        t = s.get(Task, task_id)
        if not t:
            return
        checklist = json.loads(t.checklist or "[]")
        changed = False
        for item in checklist:
            if item.get('id') == item_id:
                if item.get('done') != done:
                    item['done'] = done
                    changed = True
                break
        if changed:
            t.checklist = json.dumps(checklist)
            history = json.loads(t.history or "[]")
            history.append({"when": datetime.utcnow().isoformat(), "what": f"check_{'done' if done else 'undone'}", "by": by})
            t.history = json.dumps(history)
            s.commit()


def delete_check_item(task_id: str, item_id: str, by: str = "You"):
    with _session() as s:
        t = s.get(Task, task_id)
        if not t:
            return
        checklist = json.loads(t.checklist or "[]")
        new_list = [c for c in checklist if c.get('id') != item_id]
        if len(new_list) != len(checklist):
            t.checklist = json.dumps(new_list)
            history = json.loads(t.history or "[]")
            history.append({"when": datetime.utcnow().isoformat(), "what": "check_deleted", "by": by})
            t.history = json.dumps(history)
            s.commit()
