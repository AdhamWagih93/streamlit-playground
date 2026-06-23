"""Issue domain service: key allocation, field updates and change history."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Component,
    Issue,
    IssueType,
    Label,
    Priority,
    Project,
    Status,
    User,
    Version,
)
from app.models.activity import IssueHistory, Notification
from app.utils.ranking import rank_between
from app.utils.timetracking import parse_duration


def _now() -> datetime:
    return datetime.now(timezone.utc)


def allocate_key(db: Session, project: Project) -> tuple[str, int]:
    """Atomically reserve the next issue number for a project.

    Uses a row-level lock so concurrent creates never collide on a key.
    """
    locked = db.execute(
        select(Project).where(Project.id == project.id).with_for_update()
    ).scalar_one()
    locked.issue_counter += 1
    number = locked.issue_counter
    db.flush()
    return f"{locked.key}-{number}", number


def default_status_id(db: Session, project_id: int) -> int:
    stmt = (
        select(Status.id)
        .where((Status.project_id == project_id) | (Status.project_id.is_(None)))
        .order_by(Status.order.asc())
    )
    sid = db.scalars(stmt).first()
    if sid is None:
        raise ValueError("No statuses configured")
    return sid


def bottom_rank(db: Session, project_id: int) -> str:
    last = db.scalars(
        select(Issue.rank).where(Issue.project_id == project_id).order_by(Issue.rank.desc()).limit(1)
    ).first()
    return rank_between(last, None)


def resolve_labels(db: Session, names: list[str]) -> list[Label]:
    labels: list[Label] = []
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        label = db.scalars(select(Label).where(Label.name == name)).first()
        if not label:
            label = Label(name=name)
            db.add(label)
            db.flush()
        labels.append(label)
    return labels


def record_history(db: Session, issue: Issue, author_id: int | None, field: str, old, new) -> None:
    if str(old) == str(new):
        return
    db.add(
        IssueHistory(
            issue_id=issue.id,
            author_id=author_id,
            field=field,
            old_value=None if old is None else str(old),
            new_value=None if new is None else str(new),
            created_at=_now(),
        )
    )


def notify(db: Session, user_id: int | None, actor_id: int | None, issue: Issue, verb: str, message: str) -> None:
    if not user_id or user_id == actor_id:
        return
    db.add(
        Notification(
            user_id=user_id,
            actor_id=actor_id,
            issue_id=issue.id,
            verb=verb,
            message=message,
            created_at=_now(),
        )
    )


# Fields that map 1:1 to a column and are tracked in history by scalar value.
_SCALAR_FIELDS = {
    "summary": "summary",
    "description": "description",
    "story_points": "story_points",
    "due_date": "due_date",
    "resolution": "resolution",
}

# Foreign-key fields: (attr, model, history label, human-name attr)
_FK_FIELDS = {
    "type_id": (IssueType, "type", "name"),
    "status_id": (Status, "status", "name"),
    "priority_id": (Priority, "priority", "name"),
    "assignee_id": (User, "assignee", "display_name"),
    "reporter_id": (User, "reporter", "display_name"),
}


def apply_update(db: Session, issue: Issue, data: dict, actor_id: int | None) -> Issue:
    """Apply a partial update dict to *issue*, recording history and notifications."""
    # Scalar fields. Presence of the key in `data` means "set it" (including to
    # None); absent keys are left untouched. Callers should exclude unset fields.
    for field, attr in _SCALAR_FIELDS.items():
        if field not in data:
            continue
        old = getattr(issue, attr)
        new = data[field]
        if old != new:
            record_history(db, issue, actor_id, attr, old, new)
            setattr(issue, attr, new)
            if attr == "resolution":
                issue.resolved_at = _now() if new else None

    # FK fields
    for field, (model, label, human_attr) in _FK_FIELDS.items():
        if field in data:
            new_id = data[field]
            old_id = getattr(issue, field)
            if old_id != new_id:
                old_obj = db.get(model, old_id) if old_id else None
                new_obj = db.get(model, new_id) if new_id else None
                record_history(
                    db, issue, actor_id, label,
                    getattr(old_obj, human_attr) if old_obj else None,
                    getattr(new_obj, human_attr) if new_obj else None,
                )
                setattr(issue, field, new_id)
                if field == "assignee_id" and new_id:
                    notify(db, new_id, actor_id, issue, "assigned",
                           f"{issue.key} was assigned to you")
                if field == "status_id" and new_obj and new_obj.category == "done" and not issue.resolution:
                    issue.resolution = "Done"
                    issue.resolved_at = _now()

    # Hierarchy
    for field in ("parent_id", "epic_id", "sprint_id"):
        if field in data:
            old = getattr(issue, field)
            new = data[field]
            if old != new:
                record_history(db, issue, actor_id, field.replace("_id", ""), old, new)
                setattr(issue, field, new)

    # Time estimates (human strings)
    if "original_estimate" in data and data["original_estimate"] is not None:
        issue.original_estimate_seconds = parse_duration(data["original_estimate"])
    if "remaining_estimate" in data and data["remaining_estimate"] is not None:
        issue.remaining_estimate_seconds = parse_duration(data["remaining_estimate"])

    # Labels / components / fix versions (full-set replacement when provided)
    if data.get("label_names") is not None:
        old = sorted(l.name for l in issue.labels)
        issue.labels = resolve_labels(db, data["label_names"])
        record_history(db, issue, actor_id, "labels", ", ".join(old), ", ".join(sorted(data["label_names"])))
    if data.get("component_ids") is not None:
        issue.components = list(db.scalars(select(Component).where(Component.id.in_(data["component_ids"] or [-1]))))
    if data.get("fix_version_ids") is not None:
        issue.fix_versions = list(db.scalars(select(Version).where(Version.id.in_(data["fix_version_ids"] or [-1]))))

    db.flush()
    return issue
