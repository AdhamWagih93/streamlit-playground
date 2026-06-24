"""Analytics/insights computations over issues, statuses, types and sprints."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Board, Issue, IssueType, Priority, Project, Sprint, Status
from app.schemas.analytics import (
    CountItem,
    OverviewStats,
    ProjectStats,
    ProjectSummary,
    VelocityPoint,
)

_VELOCITY_SPRINTS = 8


def _category_counts(db: Session, project_ids: list[int] | None) -> dict[str, int]:
    q = (
        select(Status.category, func.count(Issue.id))
        .join(Status, Status.id == Issue.status_id)
        .group_by(Status.category)
    )
    if project_ids is not None:
        q = q.where(Issue.project_id.in_(project_ids or [-1]))
    rows = db.execute(q).all()
    out = {"todo": 0, "in_progress": 0, "done": 0}
    for category, count in rows:
        out[category] = out.get(category, 0) + count
    return out


def _by_status(db: Session, project_ids: list[int] | None) -> list[CountItem]:
    q = (
        select(Status.name, Status.category, Status.order, func.count(Issue.id))
        .join(Issue, Issue.status_id == Status.id)
        .group_by(Status.name, Status.category, Status.order)
        .order_by(Status.order.asc())
    )
    if project_ids is not None:
        q = q.where(Issue.project_id.in_(project_ids or [-1]))
    return [CountItem(label=n, category=c, count=cnt) for n, c, _o, cnt in db.execute(q).all()]


def _by_type(db: Session, project_ids: list[int] | None) -> list[CountItem]:
    q = (
        select(IssueType.name, IssueType.color, func.count(Issue.id))
        .join(Issue, Issue.type_id == IssueType.id)
        .group_by(IssueType.name, IssueType.color)
        .order_by(func.count(Issue.id).desc())
    )
    if project_ids is not None:
        q = q.where(Issue.project_id.in_(project_ids or [-1]))
    return [CountItem(label=n, color=c, count=cnt) for n, c, cnt in db.execute(q).all()]


def _by_priority(db: Session, project_ids: list[int] | None) -> list[CountItem]:
    q = (
        select(Priority.name, Priority.color, Priority.rank, func.count(Issue.id))
        .join(Issue, Issue.priority_id == Priority.id)
        .group_by(Priority.name, Priority.color, Priority.rank)
        .order_by(Priority.rank.asc())
    )
    if project_ids is not None:
        q = q.where(Issue.project_id.in_(project_ids or [-1]))
    return [CountItem(label=n, color=c, count=cnt) for n, c, _r, cnt in db.execute(q).all()]


def _velocity(db: Session, project_id: int) -> list[VelocityPoint]:
    # Closed sprints on this project's boards, most-recent first, then reversed
    # to chronological order for charting.
    sprints = db.scalars(
        select(Sprint)
        .join(Board, Board.id == Sprint.board_id)
        .where(Board.project_id == project_id, Sprint.state == "closed")
        .order_by(Sprint.complete_date.desc().nullslast(), Sprint.id.desc())
        .limit(_VELOCITY_SPRINTS)
    ).all()
    points: list[VelocityPoint] = []
    for sprint in reversed(sprints):
        committed = db.scalar(
            select(func.coalesce(func.sum(Issue.story_points), 0.0)).where(Issue.sprint_id == sprint.id)
        ) or 0.0
        done = db.execute(
            select(
                func.coalesce(func.sum(Issue.story_points), 0.0),
                func.count(Issue.id),
            )
            .join(Status, Status.id == Issue.status_id)
            .where(Issue.sprint_id == sprint.id, Status.category == "done")
        ).one()
        points.append(
            VelocityPoint(
                sprint_id=sprint.id,
                sprint_name=sprint.name,
                committed_points=float(committed),
                completed_points=float(done[0] or 0.0),
                completed_issues=int(done[1] or 0),
            )
        )
    return points


def project_stats(db: Session, project: Project) -> ProjectStats:
    pid = [project.id]
    cats = _category_counts(db, pid)
    total = sum(cats.values())
    closed = cats.get("done", 0)
    velocity = _velocity(db, project.id)
    n = len(velocity) or 1
    avg_pts = sum(v.completed_points for v in velocity) / n if velocity else 0.0
    avg_iss = sum(v.completed_issues for v in velocity) / n if velocity else 0.0
    return ProjectStats(
        project_id=project.id,
        project_key=project.key,
        project_name=project.name,
        total_issues=total,
        open_issues=cats.get("todo", 0),
        in_progress_issues=cats.get("in_progress", 0),
        closed_issues=closed,
        resolution_rate=(closed / total) if total else 0.0,
        by_status=_by_status(db, pid),
        by_type=_by_type(db, pid),
        by_priority=_by_priority(db, pid),
        velocity=velocity,
        avg_velocity_points=round(avg_pts, 2),
        avg_velocity_issues=round(avg_iss, 2),
    )


def _project_summary(db: Session, project: Project) -> ProjectSummary:
    cats = _category_counts(db, [project.id])
    total = sum(cats.values())
    closed = cats.get("done", 0)
    velocity = _velocity(db, project.id)
    avg_pts = (sum(v.completed_points for v in velocity) / len(velocity)) if velocity else 0.0
    return ProjectSummary(
        project_id=project.id,
        project_key=project.key,
        project_name=project.name,
        avatar_color=project.avatar_color,
        total_issues=total,
        open_issues=cats.get("todo", 0) + cats.get("in_progress", 0),
        closed_issues=closed,
        resolution_rate=(closed / total) if total else 0.0,
        avg_velocity_points=round(avg_pts, 2),
    )


def overview_stats(db: Session, project_ids: list[int] | None, scope: str) -> OverviewStats:
    """Aggregate across the given projects (None => every project)."""
    if project_ids is None:
        projects = list(db.scalars(select(Project).where(Project.is_archived.is_(False))))
    else:
        projects = list(
            db.scalars(select(Project).where(Project.id.in_(project_ids or [-1]), Project.is_archived.is_(False)))
        )
    ids = [p.id for p in projects]
    cats = _category_counts(db, ids if ids else [-1])
    total = sum(cats.values())
    closed = cats.get("done", 0)
    return OverviewStats(
        scope=scope,
        total_projects=len(projects),
        total_issues=total,
        open_issues=cats.get("todo", 0) + cats.get("in_progress", 0),
        closed_issues=closed,
        resolution_rate=(closed / total) if total else 0.0,
        by_status=_by_status(db, ids if ids else [-1]),
        by_type=_by_type(db, ids if ids else [-1]),
        projects=sorted(
            (_project_summary(db, p) for p in projects),
            key=lambda s: s.total_issues,
            reverse=True,
        ),
    )
