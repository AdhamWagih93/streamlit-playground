"""Analytics/insights computations over issues, statuses, types and sprints.

Beyond descriptive stats (counts, breakdowns, velocity) this module computes
"needs attention" signals — overdue, high-priority, blocked, unassigned and
stale work, plus active-sprint risk — so the UI can lead with what to act on now.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import Board, Component, Issue, IssueLink, IssueType, Priority, Project, Sprint, Status
from app.models.issue import issue_components
from app.schemas.analytics import (
    AttentionIssue,
    AttentionItem,
    CountItem,
    OverviewStats,
    ProjectStats,
    ProjectSummary,
    SprintHealth,
    VelocityPoint,
    Window,
)

_VELOCITY_SPRINTS = 8
_STALE_DAYS = 14        # open & untouched this long
_STALE_WIP_DAYS = 7     # in-progress & untouched this long
_HIGH_PRIORITY_RANK = 2  # Highest(1) / High(2)
_LOW_PRIORITY_RANK = 4   # Low(4) / Lowest(5)

# Weights for the per-project attention score (drives global ranking).
# Low-priority backlog is guidance, not urgency -> weight 0 (it never inflates
# the score); low-priority work that's actively in progress is mild waste -> 1.
_W = {"overdue": 4, "blocked": 3, "high_priority": 3, "open_bugs": 2,
      "unassigned": 1, "stale": 1, "stale_wip": 2, "at_risk_sprint": 6,
      "low_priority": 0, "low_priority_wip": 1}


def _apply_window(q, start: datetime | None, end: datetime | None):
    """Scope a query to issues created within [start, end) (either may be None)."""
    if start is not None:
        q = q.where(Issue.created_at >= start)
    if end is not None:
        q = q.where(Issue.created_at < end)
    return q


def _category_counts(
    db: Session, project_ids: list[int] | None,
    start: datetime | None = None, end: datetime | None = None,
) -> dict[str, int]:
    q = (
        select(Status.category, func.count(Issue.id))
        .join(Status, Status.id == Issue.status_id)
        .group_by(Status.category)
    )
    if project_ids is not None:
        q = q.where(Issue.project_id.in_(project_ids or [-1]))
    q = _apply_window(q, start, end)
    rows = db.execute(q).all()
    out = {"todo": 0, "in_progress": 0, "done": 0}
    for category, count in rows:
        out[category] = out.get(category, 0) + count
    return out


def _by_status(
    db: Session, project_ids: list[int] | None,
    start: datetime | None = None, end: datetime | None = None,
) -> list[CountItem]:
    q = (
        select(Status.name, Status.category, Status.order, func.count(Issue.id))
        .join(Issue, Issue.status_id == Status.id)
        .group_by(Status.name, Status.category, Status.order)
        .order_by(Status.order.asc())
    )
    if project_ids is not None:
        q = q.where(Issue.project_id.in_(project_ids or [-1]))
    q = _apply_window(q, start, end)
    return [CountItem(label=n, category=c, count=cnt) for n, c, _o, cnt in db.execute(q).all()]


def _by_type(
    db: Session, project_ids: list[int] | None,
    start: datetime | None = None, end: datetime | None = None,
) -> list[CountItem]:
    q = (
        select(IssueType.name, IssueType.color, func.count(Issue.id))
        .join(Issue, Issue.type_id == IssueType.id)
        .group_by(IssueType.name, IssueType.color)
        .order_by(func.count(Issue.id).desc())
    )
    if project_ids is not None:
        q = q.where(Issue.project_id.in_(project_ids or [-1]))
    q = _apply_window(q, start, end)
    return [CountItem(label=n, color=c, count=cnt) for n, c, cnt in db.execute(q).all()]


def _by_priority(
    db: Session, project_ids: list[int] | None,
    start: datetime | None = None, end: datetime | None = None,
) -> list[CountItem]:
    q = (
        select(Priority.name, Priority.color, Priority.rank, func.count(Issue.id))
        .join(Issue, Issue.priority_id == Priority.id)
        .group_by(Priority.name, Priority.color, Priority.rank)
        .order_by(Priority.rank.asc())
    )
    if project_ids is not None:
        q = q.where(Issue.project_id.in_(project_ids or [-1]))
    q = _apply_window(q, start, end)
    return [CountItem(label=n, color=c, count=cnt) for n, c, _r, cnt in db.execute(q).all()]


def _by_component(
    db: Session, project_ids: list[int] | None,
    start: datetime | None = None, end: datetime | None = None,
) -> list[CountItem]:
    """Issue counts per component, plus a synthetic "No component" bucket so
    uncategorised work is visible and triageable."""
    q = (
        select(Component.name, func.count(Issue.id))
        .join(issue_components, issue_components.c.component_id == Component.id)
        .join(Issue, Issue.id == issue_components.c.issue_id)
        .group_by(Component.name)
        .order_by(func.count(Issue.id).desc())
    )
    if project_ids is not None:
        q = q.where(Issue.project_id.in_(project_ids or [-1]))
    q = _apply_window(q, start, end)
    items = [CountItem(label=n, count=cnt) for n, cnt in db.execute(q).all()]

    # Issues with no component at all (an issue may carry several components, so
    # this is counted directly rather than derived from the totals above).
    nq = select(func.count(Issue.id)).where(
        ~Issue.id.in_(select(issue_components.c.issue_id))
    )
    if project_ids is not None:
        nq = nq.where(Issue.project_id.in_(project_ids or [-1]))
    nq = _apply_window(nq, start, end)
    no_component = db.scalar(nq) or 0
    if no_component:
        items.append(CountItem(label="No component", count=int(no_component)))
    return items


def _velocity(
    db: Session, project_id: int,
    start: datetime | None = None, end: datetime | None = None,
) -> list[VelocityPoint]:
    # Closed sprints on this project's boards, most-recent first, then reversed
    # to chronological order for charting. When a window is set, only sprints
    # completed within it are included.
    q = (
        select(Sprint)
        .join(Board, Board.id == Sprint.board_id)
        .where(Board.project_id == project_id, Sprint.state == "closed")
    )
    if start is not None:
        q = q.where(Sprint.complete_date >= start)
    if end is not None:
        q = q.where(Sprint.complete_date < end)
    sprints = db.scalars(
        q.order_by(Sprint.complete_date.desc().nullslast(), Sprint.id.desc()).limit(_VELOCITY_SPRINTS)
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


# --- "Needs attention" engine ---------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _open_issues(db: Session, project_id: int) -> list[Issue]:
    return list(
        db.scalars(
            select(Issue)
            .join(Status, Status.id == Issue.status_id)
            .where(Issue.project_id == project_id, Status.category != "done")
            .options(
                joinedload(Issue.priority), joinedload(Issue.assignee),
                joinedload(Issue.status), joinedload(Issue.type),
            )
        )
    )


def _attn_issue(i: Issue, now: datetime) -> AttentionIssue:
    days_overdue = None
    if i.due_date:
        d = (now.date() - i.due_date).days
        days_overdue = d if d > 0 else None
    return AttentionIssue(
        key=i.key, summary=i.summary,
        priority=i.priority.name if i.priority else None,
        priority_color=i.priority.color if i.priority else None,
        assignee=i.assignee.display_name if i.assignee else None,
        status=i.status.name if i.status else None,
        due_date=i.due_date, days_overdue=days_overdue, updated_at=i.updated_at,
    )


def _sprint_health(db: Session, project_id: int, now: datetime) -> SprintHealth | None:
    sprint = db.scalars(
        select(Sprint).join(Board, Board.id == Sprint.board_id)
        .where(Board.project_id == project_id, Sprint.state == "active")
        .order_by(Sprint.id.desc()).limit(1)
    ).first()
    if not sprint:
        return None
    total_pts = db.scalar(
        select(func.coalesce(func.sum(Issue.story_points), 0.0)).where(Issue.sprint_id == sprint.id)
    ) or 0.0
    done_pts, done_cnt = db.execute(
        select(func.coalesce(func.sum(Issue.story_points), 0.0), func.count(Issue.id))
        .join(Status, Status.id == Issue.status_id)
        .where(Issue.sprint_id == sprint.id, Status.category == "done")
    ).one()
    total_cnt = db.scalar(select(func.count(Issue.id)).where(Issue.sprint_id == sprint.id)) or 0
    done_pts = float(done_pts or 0.0)
    done_cnt = int(done_cnt or 0)
    incomplete = int(total_cnt) - done_cnt
    pct = (done_pts / total_pts) if total_pts > 0 else ((done_cnt / total_cnt) if total_cnt else 0.0)
    days_remaining = (sprint.end_date.date() - now.date()).days if sprint.end_date else None
    at_risk, reason = False, None
    if days_remaining is not None and incomplete > 0:
        if days_remaining < 0:
            at_risk, reason = True, f"Ended {abs(days_remaining)}d ago with {incomplete} unfinished"
        elif days_remaining <= 3 and pct < 0.7:
            at_risk, reason = True, f"{days_remaining}d left · {round(pct * 100)}% done · {incomplete} unfinished"
    return SprintHealth(
        sprint_id=sprint.id, name=sprint.name, goal=sprint.goal, end_date=sprint.end_date,
        days_remaining=days_remaining, total_points=float(total_pts), completed_points=done_pts,
        percent_complete=round(pct, 3), incomplete_issues=incomplete, at_risk=at_risk, risk_reason=reason,
    )


def compute_attention(db: Session, project: Project, with_samples: bool = True) -> dict:
    """Compute the per-project attention signals, score and active-sprint risk."""
    now = _now()
    today = now.date()
    opens = _open_issues(db, project.id)
    open_ids = [i.id for i in opens]
    blocked_ids: set[int] = set()
    if open_ids:
        blocked_ids = set(
            db.scalars(
                select(IssueLink.target_id).where(
                    IssueLink.link_type == "blocks", IssueLink.target_id.in_(open_ids)
                )
            )
        )
    stale_cut = now - timedelta(days=_STALE_DAYS)
    wip_cut = now - timedelta(days=_STALE_WIP_DAYS)

    groups: dict[str, list[Issue]] = {
        "overdue": [i for i in opens if i.due_date and i.due_date < today],
        "high_priority": [i for i in opens if i.priority and i.priority.rank <= _HIGH_PRIORITY_RANK],
        "blocked": [i for i in opens if i.id in blocked_ids],
        "open_bugs": [i for i in opens if i.type and i.type.name.lower() == "bug"],
        "unassigned": [i for i in opens if i.assignee_id is None],
        "stale": [i for i in opens if i.updated_at and i.updated_at < stale_cut],
        "stale_wip": [
            i for i in opens
            if i.status and i.status.category == "in_progress" and i.updated_at and i.updated_at < wip_cut
        ],
        "low_priority": [i for i in opens if i.priority and i.priority.rank >= _LOW_PRIORITY_RANK],
    }
    # Low-priority work that's actively in progress — effort going to low-value
    # work while higher-priority items wait.
    groups["low_priority_wip"] = [
        i for i in groups["low_priority"]
        if i.status and i.status.category == "in_progress"
    ]
    sprint = _sprint_health(db, project.id, now)
    score = sum(_W[k] * len(v) for k, v in groups.items())
    if sprint and sprint.at_risk:
        score += _W["at_risk_sprint"]

    k = project.key
    meta = {
        "overdue": ("Overdue", "Past their due date and not done", "high",
                    f"project = {k} AND due < 0d AND statusCategory != done ORDER BY due ASC"),
        "high_priority": ("High priority open", "Highest/High priority, still open", "high",
                    f"project = {k} AND priority IN (Highest, High) AND statusCategory != done"),
        "blocked": ("Blocked", "Open issues blocked by another issue", "high", None),
        "unassigned": ("Unassigned", "Open work with no owner", "medium",
                    f"project = {k} AND assignee = empty AND statusCategory != done"),
        "stale_wip": ("Stuck in progress", f"In progress, untouched {_STALE_WIP_DAYS}+ days", "medium",
                    f"project = {k} AND statusCategory = in_progress AND updated < -{_STALE_WIP_DAYS}d"),
        "open_bugs": ("Open bugs", "Bugs not yet resolved", "medium",
                    f"project = {k} AND type = Bug AND statusCategory != done"),
        "stale": ("Stale", f"Open, untouched {_STALE_DAYS}+ days", "low",
                    f"project = {k} AND updated < -{_STALE_DAYS}d AND statusCategory != done ORDER BY updated ASC"),
        "low_priority_wip": ("Low priority, in progress",
                    "Low-priority work taking active effort — re-prioritise or pause for higher-value work", "medium",
                    f"project = {k} AND priority IN (Low, Lowest) AND statusCategory = in_progress"),
        "low_priority": ("Low priority backlog",
                    "Defer, batch or close these to keep focus on higher-priority work", "low",
                    f"project = {k} AND priority IN (Low, Lowest) AND statusCategory != done ORDER BY updated ASC"),
    }

    def _samples(key: str, items: list[Issue]) -> list[AttentionIssue]:
        if not with_samples:
            return []
        if key == "overdue":
            items = sorted(items, key=lambda i: (i.due_date or today))
        elif key == "high_priority":
            items = sorted(items, key=lambda i: (i.priority.rank if i.priority else 99, i.due_date or today))
        else:
            items = sorted(items, key=lambda i: (i.updated_at or now))
        return [_attn_issue(i, now) for i in items[:5]]

    order = ["overdue", "high_priority", "blocked", "unassigned", "stale_wip",
             "open_bugs", "low_priority_wip", "stale", "low_priority"]
    items: list[AttentionItem] = []
    for key in order:
        grp = groups[key]
        if not grp:
            continue
        label, desc, sev, tql = meta[key]
        items.append(AttentionItem(key=key, label=label, description=desc, count=len(grp),
                                   severity=sev, tql=tql, samples=_samples(key, grp)))

    reasons: list[str] = []
    if groups["overdue"]:
        reasons.append(f"{len(groups['overdue'])} overdue")
    if groups["high_priority"]:
        reasons.append(f"{len(groups['high_priority'])} high-priority")
    if groups["blocked"]:
        reasons.append(f"{len(groups['blocked'])} blocked")
    if sprint and sprint.at_risk:
        reasons.append("sprint at risk")
    if groups["unassigned"] and len(reasons) < 3:
        reasons.append(f"{len(groups['unassigned'])} unassigned")

    return {"items": items, "score": score, "sprint": sprint, "groups": groups,
            "reasons": reasons[:3], "now": now}


def project_stats(
    db: Session, project: Project,
    start: datetime | None = None, end: datetime | None = None, window: Window | None = None,
) -> ProjectStats:
    pid = [project.id]
    cats = _category_counts(db, pid, start, end)
    total = sum(cats.values())
    closed = cats.get("done", 0)
    velocity = _velocity(db, project.id, start, end)
    n = len(velocity) or 1
    avg_pts = sum(v.completed_points for v in velocity) / n if velocity else 0.0
    avg_iss = sum(v.completed_issues for v in velocity) / n if velocity else 0.0
    # Attention is always current-state — it ignores the descriptive time window.
    att = compute_attention(db, project, with_samples=True)
    return ProjectStats(
        project_id=project.id,
        project_key=project.key,
        project_name=project.name,
        window=window or Window(),
        total_issues=total,
        open_issues=cats.get("todo", 0),
        in_progress_issues=cats.get("in_progress", 0),
        closed_issues=closed,
        resolution_rate=(closed / total) if total else 0.0,
        attention=att["items"],
        attention_score=att["score"],
        sprint_health=att["sprint"],
        by_status=_by_status(db, pid, start, end),
        by_type=_by_type(db, pid, start, end),
        by_priority=_by_priority(db, pid, start, end),
        by_component=_by_component(db, pid, start, end),
        velocity=velocity,
        avg_velocity_points=round(avg_pts, 2),
        avg_velocity_issues=round(avg_iss, 2),
    )


def _project_summary(
    db: Session, project: Project, att: dict,
    start: datetime | None = None, end: datetime | None = None,
) -> ProjectSummary:
    cats = _category_counts(db, [project.id], start, end)
    total = sum(cats.values())
    closed = cats.get("done", 0)
    velocity = _velocity(db, project.id, start, end)
    avg_pts = (sum(v.completed_points for v in velocity) / len(velocity)) if velocity else 0.0
    g = att["groups"]
    sprint = att["sprint"]
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
        attention_score=att["score"],
        overdue=len(g["overdue"]),
        high_priority_open=len(g["high_priority"]),
        unassigned_open=len(g["unassigned"]),
        blocked=len(g["blocked"]),
        at_risk_sprint=bool(sprint and sprint.at_risk),
        needs_attention=att["score"] > 0,
        top_reasons=att["reasons"],
    )


def overview_stats(
    db: Session, project_ids: list[int] | None, scope: str,
    start: datetime | None = None, end: datetime | None = None, window: Window | None = None,
) -> OverviewStats:
    """Aggregate across the given projects (None => every project)."""
    if project_ids is None:
        projects = list(db.scalars(select(Project).where(Project.is_archived.is_(False))))
    else:
        projects = list(
            db.scalars(select(Project).where(Project.id.in_(project_ids or [-1]), Project.is_archived.is_(False)))
        )
    ids = [p.id for p in projects]
    cats = _category_counts(db, ids if ids else [-1], start, end)
    total = sum(cats.values())
    closed = cats.get("done", 0)

    summaries: list[ProjectSummary] = []
    top_pool: list[tuple[int, float, AttentionIssue]] = []  # (bucket, sortkey, issue)
    tot_overdue = tot_unassigned = tot_high = tot_blocked = at_risk = needs = 0
    for p in projects:
        att = compute_attention(db, p, with_samples=True)
        summaries.append(_project_summary(db, p, att, start, end))
        g = att["groups"]
        now = att["now"]
        tot_overdue += len(g["overdue"])
        tot_unassigned += len(g["unassigned"])
        tot_high += len(g["high_priority"])
        tot_blocked += len(g["blocked"])
        if att["sprint"] and att["sprint"].at_risk:
            at_risk += 1
        if att["score"] > 0:
            needs += 1
        # Cross-project most-urgent issues: overdue (by days overdue), then high-priority.
        for i in g["overdue"]:
            d = (now.date() - i.due_date).days if i.due_date else 0
            top_pool.append((0, -d, _attn_issue(i, now)))
        for i in g["high_priority"]:
            top_pool.append((1, (i.priority.rank if i.priority else 99), _attn_issue(i, now)))

    top_pool.sort(key=lambda t: (t[0], t[1]))
    top_attention = [t[2] for t in top_pool[:8]]

    summaries.sort(key=lambda s: (s.attention_score, s.total_issues), reverse=True)
    return OverviewStats(
        scope=scope,
        window=window or Window(),
        total_projects=len(projects),
        total_issues=total,
        open_issues=cats.get("todo", 0) + cats.get("in_progress", 0),
        closed_issues=closed,
        resolution_rate=(closed / total) if total else 0.0,
        total_overdue=tot_overdue,
        total_unassigned_open=tot_unassigned,
        total_high_priority_open=tot_high,
        total_blocked=tot_blocked,
        projects_at_risk=at_risk,
        projects_needing_attention=needs,
        top_attention=top_attention,
        by_status=_by_status(db, ids if ids else [-1]),
        by_type=_by_type(db, ids if ids else [-1]),
        projects=summaries,
    )
