"""The whole picture on one screen — every section is failure-isolated so a
broken integration dims its panel instead of blanking the page."""

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import settings
from ..db import RepoAction, User, XPEvent, get_db, utcnow
from ..gamification import team_quest_progress
from ..integrations import elastic, jenkins, jira
from ..ticket_sync import sync_closed_tickets

router = APIRouter(prefix="/api", tags=["overview"])


def _jira_section() -> dict:
    b = jira.board()
    issues = [i for c in b["columns"] for i in c["issues"]]
    open_issues = [i for i in issues if i["status"].lower() not in settings.done_statuses]

    today = utcnow().date()
    due_soon = overdue = 0
    for i in open_issues:
        if not i.get("due"):
            continue
        try:
            days = (dt.date.fromisoformat(i["due"]) - today).days
        except ValueError:
            continue
        if days < 0:
            overdue += 1
        elif days <= 2:
            due_soon += 1

    per = {o: {"name": o, "open": 0, "closed_recent": 0} for o in jira.list_objectives()}
    for i in issues:
        bucket = ("open" if i["status"].lower() not in settings.done_statuses
                  else "closed_recent")
        for c in i.get("components") or []:
            if c in per:
                per[c][bucket] += 1

    return {
        "source": b["source"], "project": b["project"],
        "columns": [{"name": c["name"], "label": c.get("label") or c["name"],
                     "count": len(c["issues"])} for c in b["columns"]],
        "open_total": len(open_issues),
        "unassigned": sum(1 for i in open_issues if not i["assignee"]),
        "reopened": sum(1 for i in issues
                        if i["status"].lower() in settings.reopened_statuses),
        "missing_objective": sum(1 for i in open_issues if not i.get("components")),
        "due_soon": due_soon, "overdue": overdue,
        "objectives": sorted(per.values(), key=lambda o: -o["open"]),
    }


def _kpi_section(ci_failures: list[dict]) -> dict:
    last_sync, next_sync = elastic.sync_times()
    now = elastic._now()
    docs = elastic.kpi_recent(hours=24)["docs"]
    ok = sum(1 for d in docs if str(d.get("status", "")).upper() == "SUCCESS")
    since_last_min = (now - last_sync).total_seconds() / 60
    return {
        "source": ("live" if elastic.is_live()
                   else ("demo" if settings.demo_mode else "not configured")),
        "total": len(docs), "success": ok,
        "overall_pct": round(ok / len(docs) * 100, 1) if docs else 0.0,
        "seconds_remaining": max(0, int((next_sync - now).total_seconds())),
        "next_sync": next_sync.isoformat(),
        "at_risk": sum(1 for f in ci_failures if f["ago_min"] <= since_last_min),
    }


@router.get("/overview/cursor")
def overview_cursor(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Cheap change beacon the Overview polls: bumps whenever any member's
    action lands an XPEvent or a repo action changes state. External
    Jira/Jenkins/ES drift is covered by the frontend's slow full refresh."""
    last_event = db.query(func.max(XPEvent.id)).scalar() or 0
    actions = db.query(func.count(RepoAction.id)).scalar() or 0
    pending = db.query(func.count(RepoAction.id)).filter(
        RepoAction.status == "pending_approval").scalar() or 0
    return {"cursor": f"{last_event}:{actions}:{pending}"}


@router.get("/overview")
def overview(user: User = Depends(current_user), db: Session = Depends(get_db)):
    sync_closed_tickets(db)  # Jira-side closures count toward team pulse
    out: dict = {"generated_at": utcnow().isoformat()}

    try:
        out["jira"] = _jira_section()
    except Exception as exc:  # noqa: BLE001 — dim the panel, keep the page
        out["jira"] = {"source": "error", "error": str(exc)[:200],
                       "columns": [], "objectives": [], "open_total": 0,
                       "unassigned": 0, "reopened": 0, "missing_objective": 0,
                       "due_soon": 0, "overdue": 0}

    ci_failures: list[dict] = []
    try:
        ci = jenkins.overview()
        ci_failures = ci["failures"]
        out["ci"] = {"source": ci["source"],
                     "failure_window_days": ci["failure_window_days"],
                     "failures": len(ci["failures"]),
                     "long_running": len(ci["long_running"]),
                     "top_failures": ci["failures"][:3],
                     "stuck": ci["long_running"][:3]}
    except Exception as exc:  # noqa: BLE001
        out["ci"] = {"source": "error", "error": str(exc)[:200], "failures": 0,
                     "long_running": 0, "top_failures": [], "stuck": [],
                     "failure_window_days": settings.jenkins_failure_window_days}

    try:
        out["kpi"] = _kpi_section(ci_failures)
    except Exception as exc:  # noqa: BLE001
        out["kpi"] = {"source": "error", "error": str(exc)[:200], "total": 0,
                      "success": 0, "overall_pct": 0.0, "at_risk": 0,
                      "seconds_remaining": 0, "next_sync": None}

    out["approvals"] = {"pending": db.query(func.count(RepoAction.id)).filter(
        RepoAction.status == "pending_approval").scalar() or 0}

    now = utcnow()
    week, prev = now - dt.timedelta(days=7), now - dt.timedelta(days=14)

    def _window(start: dt.datetime, end: dt.datetime) -> dict:
        events = db.query(XPEvent).filter(XPEvent.created_at >= start,
                                          XPEvent.created_at < end).all()
        by_kind: dict[str, int] = {}
        for e in events:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        return {"xp": sum(e.points for e in events),
                "tickets_done": by_kind.get("ticket_done", 0),
                "builds_fixed": by_kind.get("build_fixed", 0)}

    top3 = (db.query(XPEvent.username, func.coalesce(func.sum(XPEvent.points), 0))
            .filter(XPEvent.created_at >= week).group_by(XPEvent.username)
            .order_by(func.sum(XPEvent.points).desc()).limit(3).all())
    names = {u.username: u.display_name for u in db.query(User).all()}
    out["team"] = {"this_week": _window(week, now), "last_week": _window(prev, week),
                   "top3": [{"username": r[0],
                             "display_name": names.get(r[0], r[0]),
                             "xp": int(r[1])} for r in top3],
                   "quests": team_quest_progress(db)}

    events = (db.query(XPEvent).order_by(XPEvent.created_at.desc()).limit(8).all())
    out["activity"] = [{"username": e.username, "kind": e.kind, "points": e.points,
                        "message": e.message, "at": e.created_at.isoformat()}
                       for e in events]
    return out
