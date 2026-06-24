"""First-run database bootstrap: create tables and seed global defaults."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_password
from app.models import IssueType, Priority, Status, User

log = logging.getLogger("trackly.bootstrap")

# Global (project-less) defaults applied to every project.
DEFAULT_ISSUE_TYPES = [
    {"name": "Epic", "icon": "epic", "color": "#8b5cf6", "is_subtask": False},
    {"name": "Story", "icon": "story", "color": "#22c55e", "is_subtask": False},
    {"name": "Task", "icon": "task", "color": "#3b82f6", "is_subtask": False},
    {"name": "Bug", "icon": "bug", "color": "#ef4444", "is_subtask": False},
    {"name": "Sub-task", "icon": "subtask", "color": "#3b82f6", "is_subtask": True},
]

DEFAULT_STATUSES = [
    {"name": "To Do", "category": "todo", "order": 0},
    {"name": "In Progress", "category": "in_progress", "order": 1},
    {"name": "In Review", "category": "in_progress", "order": 2},
    {"name": "Done", "category": "done", "order": 3},
]

DEFAULT_PRIORITIES = [
    {"name": "Highest", "icon": "highest", "color": "#d1453b", "rank": 1},
    {"name": "High", "icon": "high", "color": "#e9594b", "rank": 2},
    {"name": "Medium", "icon": "medium", "color": "#f59e0b", "rank": 3},
    {"name": "Low", "icon": "low", "color": "#2e7be4", "rank": 4},
    {"name": "Lowest", "icon": "lowest", "color": "#5e8bff", "rank": 5},
]


def create_all_tables() -> None:
    # Importing app.models (done above) registers every table on Base.metadata.
    Base.metadata.create_all(bind=engine)


def seed_defaults(db: Session) -> None:
    if not db.scalars(select(IssueType).where(IssueType.project_id.is_(None))).first():
        db.add_all([IssueType(**t) for t in DEFAULT_ISSUE_TYPES])
    if not db.scalars(select(Status).where(Status.project_id.is_(None))).first():
        db.add_all([Status(**s) for s in DEFAULT_STATUSES])
    if not db.scalars(select(Priority)).first():
        db.add_all([Priority(**p) for p in DEFAULT_PRIORITIES])
    db.commit()


def seed_admin(db: Session) -> None:
    existing = db.scalars(select(User).where(User.email == settings.bootstrap_admin_email)).first()
    if existing:
        return
    admin = User(
        username=settings.bootstrap_admin_username,
        email=settings.bootstrap_admin_email,
        display_name="Administrator",
        password_hash=hash_password(settings.bootstrap_admin_password),
        is_admin=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    log.info("Created bootstrap admin user %s", settings.bootstrap_admin_email)


def run_bootstrap() -> None:
    from app.core.bootstrap_rbac import run_rbac_bootstrap
    from app.core.schema_sync import reconcile_schema

    create_all_tables()
    # Additively add any new columns to pre-existing tables so schema changes
    # never require dropping the database (see app.core.schema_sync).
    reconcile_schema(engine)
    with SessionLocal() as db:
        seed_defaults(db)
        seed_admin(db)
        run_rbac_bootstrap(db)
