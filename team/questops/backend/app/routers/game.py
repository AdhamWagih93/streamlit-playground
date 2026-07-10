"""Leaderboard, badges, activity history and weekly recap."""

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import current_user, sync_group_members
from ..db import BadgeAward, User, XPEvent, get_db, utcnow
from ..gamification import BADGES, TEAM_BADGES, level_info

router = APIRouter(prefix="/api", tags=["game"])


def _since_for(window: str) -> dt.datetime | None:
    """window: number of days ('7', '30', …), 'week' (legacy) or 'all'."""
    if window == "all":
        return None
    if window == "week":
        window = "7"
    days = int("".join(ch for ch in window if ch.isdigit()) or 7)
    return utcnow() - dt.timedelta(days=days)


@router.get("/leaderboard")
def leaderboard(window: str = "7", user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    sync_group_members(db)  # everyone in the LDAP group appears, XP or not
    users = db.query(User).all()
    since = _since_for(window)

    # per-member activity counts for the window, one grouped query
    kq = db.query(XPEvent.username, XPEvent.kind, func.count(XPEvent.id))
    if since is not None:
        kq = kq.filter(XPEvent.created_at >= since)
    counts: dict[str, dict[str, int]] = {}
    for uname, kind, n in kq.group_by(XPEvent.username, XPEvent.kind):
        counts.setdefault(uname, {})[kind] = n

    rows = []
    for u in users:
        q = db.query(func.coalesce(func.sum(XPEvent.points), 0)).filter(
            XPEvent.username == u.username)
        if since is not None:
            q = q.filter(XPEvent.created_at >= since)
        window_xp = q.scalar() or 0
        badge_count = db.query(func.count(BadgeAward.id)).filter(
            BadgeAward.username == u.username).scalar() or 0
        c = counts.get(u.username, {})
        rows.append({"username": u.username, "display_name": u.display_name,
                     "role": u.role, "xp": window_xp if since else u.xp,
                     "total_xp": u.xp, "streak": u.streak,
                     "level": level_info(u.xp), "badges": badge_count,
                     "stats": {"tickets_done": c.get("ticket_done", 0),
                               "resolved": c.get("ticket_resolved", 0),
                               "builds_fixed": c.get("build_fixed", 0),
                               "reviews": c.get("approval_review", 0),
                               "actions": c.get("repo_action_executed", 0)}})
    rows.sort(key=lambda r: -r["xp"])
    return {"window": window, "rows": rows}


@router.get("/members")
def members(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Lightweight roster for pickers (e.g. quick-add assignee)."""
    sync_group_members(db)
    rows = db.query(User).order_by(User.username).all()
    return {"members": [{"username": u.username, "display_name": u.display_name}
                        for u in rows]}


@router.get("/history")
def history(username: str = "", limit: int = 60, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    target = username or user.username
    events = (db.query(XPEvent).filter(XPEvent.username == target)
              .order_by(XPEvent.created_at.desc()).limit(limit).all())
    # daily totals for the last 28 days (sparkline)
    start = utcnow() - dt.timedelta(days=28)
    daily: dict[str, int] = {}
    for e in db.query(XPEvent).filter(XPEvent.username == target,
                                      XPEvent.created_at >= start):
        day = e.created_at.date().isoformat()
        daily[day] = daily.get(day, 0) + e.points
    days = [(start + dt.timedelta(days=i)).date().isoformat() for i in range(29)]
    return {
        "username": target,
        "events": [{"kind": e.kind, "points": e.points, "message": e.message,
                    "ref": e.ref, "at": e.created_at.isoformat()} for e in events],
        "daily": [{"day": d, "xp": daily.get(d, 0)} for d in days],
    }


@router.get("/activity")
def activity(days: int = 7, limit: int = 200, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    """Team-wide activity feed for the selected time window."""
    since = utcnow() - dt.timedelta(days=days)
    events = (db.query(XPEvent).filter(XPEvent.created_at >= since)
              .order_by(XPEvent.created_at.desc()).limit(limit).all())
    return {"days": days,
            "events": [{"username": e.username, "kind": e.kind, "points": e.points,
                        "message": e.message, "at": e.created_at.isoformat()}
                       for e in events]}


@router.get("/recap")
def recap(days: int = 7, user: User = Depends(current_user), db: Session = Depends(get_db)):
    now = utcnow()
    this_start = now - dt.timedelta(days=days)
    last_start = now - dt.timedelta(days=days * 2)

    def stats(start: dt.datetime, end: dt.datetime) -> dict:
        events = db.query(XPEvent).filter(XPEvent.created_at >= start,
                                          XPEvent.created_at < end).all()
        by_kind: dict[str, int] = {}
        by_user: dict[str, int] = {}
        for e in events:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
            by_user[e.username] = by_user.get(e.username, 0) + e.points
        top = max(by_user.items(), key=lambda kv: kv[1]) if by_user else ("—", 0)
        return {"xp": sum(e.points for e in events),
                "tickets_done": by_kind.get("ticket_done", 0),
                "builds_fixed": by_kind.get("build_fixed", 0),
                "reviews": by_kind.get("approval_review", 0),
                "top_user": top[0], "top_xp": top[1]}

    return {"this_week": stats(this_start, now), "last_week": stats(last_start, this_start)}


@router.get("/badges")
def badges(user: User = Depends(current_user), db: Session = Depends(get_db)):
    awards = db.query(BadgeAward).all()
    holders: dict[str, list[str]] = {}
    for a in awards:
        holders.setdefault(a.key, []).append(a.username)
    return {"catalog": [{**b, "holders": holders.get(b["key"], [])}
                        for b in BADGES + TEAM_BADGES]}
