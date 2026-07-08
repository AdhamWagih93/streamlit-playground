"""Leaderboard, badges, activity history and weekly recap."""

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import BadgeAward, User, XPEvent, get_db, utcnow
from ..gamification import BADGES, level_info

router = APIRouter(prefix="/api", tags=["game"])


@router.get("/leaderboard")
def leaderboard(window: str = "week", user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    users = db.query(User).all()
    since = None
    if window == "week":
        since = utcnow() - dt.timedelta(days=7)

    rows = []
    for u in users:
        q = db.query(func.coalesce(func.sum(XPEvent.points), 0)).filter(
            XPEvent.username == u.username)
        if since is not None:
            q = q.filter(XPEvent.created_at >= since)
        window_xp = q.scalar() or 0
        badge_count = db.query(func.count(BadgeAward.id)).filter(
            BadgeAward.username == u.username).scalar() or 0
        rows.append({"username": u.username, "display_name": u.display_name,
                     "role": u.role, "xp": window_xp if since else u.xp,
                     "total_xp": u.xp, "streak": u.streak,
                     "level": level_info(u.xp), "badges": badge_count})
    rows.sort(key=lambda r: -r["xp"])
    return {"window": window, "rows": rows}


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


@router.get("/recap")
def recap(user: User = Depends(current_user), db: Session = Depends(get_db)):
    now = utcnow()
    this_start = now - dt.timedelta(days=7)
    last_start = now - dt.timedelta(days=14)

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
    return {"catalog": [{**b, "holders": holders.get(b["key"], [])} for b in BADGES]}
