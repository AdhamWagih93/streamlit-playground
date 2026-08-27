"""Activity page — high-level QuestOps usage from the local database.

ActivityEvent rows (logins + page views) plus the XPEvent stream (work
actions) fold into per-user / per-page / per-team / per-day aggregates.
Nothing here touches an external system.
"""

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import ActivityEvent, User, XPEvent, get_db, utcnow

router = APIRouter(prefix="/api/usage", tags=["activity"])


class TrackBody(BaseModel):
    page: str


@router.post("/track")
def track(body: TrackBody, user: User = Depends(current_user),
          db: Session = Depends(get_db)):
    """One page-view event per navigation; repeats of the same page by the
    same user within a minute are collapsed."""
    page = (body.page or "").strip().lower()[:60]
    if not page:
        return {"ok": False}
    recent = (db.query(ActivityEvent)
              .filter(ActivityEvent.username == user.username,
                      ActivityEvent.kind == "page",
                      ActivityEvent.page == page,
                      ActivityEvent.at >= utcnow() - dt.timedelta(seconds=60))
              .first())
    if recent is None:
        db.add(ActivityEvent(username=user.username, kind="page", page=page))
        db.commit()
    return {"ok": True}


@router.get("")
def activity(days: int = 30, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    days = max(1, min(int(days or 30), 365))
    since = utcnow() - dt.timedelta(days=days)
    rows = (db.query(ActivityEvent).filter(ActivityEvent.at >= since)
            .order_by(ActivityEvent.at.desc()).limit(20000).all())
    xp = (db.query(XPEvent).filter(XPEvent.created_at >= since)
          .order_by(XPEvent.created_at.desc()).limit(20000).all())
    display = {u.username: u.display_name or u.username for u in db.query(User)}

    per_user: dict = {}
    per_page: dict = {}
    per_day: dict = {}
    per_hour = [0] * 24
    logins = views = 0
    for r in rows:
        u = per_user.setdefault(r.username, {"key": r.username,
                                             "display": display.get(r.username, r.username),
                                             "count": 0, "logins": 0, "views": 0,
                                             "actions": 0})
        u["count"] += 1
        if r.kind == "login":
            u["logins"] += 1
            logins += 1
        elif r.kind == "page":
            u["views"] += 1
            views += 1
            per_page[r.page] = per_page.get(r.page, 0) + 1
        day = per_day.setdefault(r.at.strftime("%Y-%m-%d"), {"count": 0, "by_user": {}})
        day["count"] += 1
        day["by_user"][r.username] = day["by_user"].get(r.username, 0) + 1
        per_hour[r.at.hour] += 1
    actions = 0
    for e in xp:
        if e.kind == "quest_bonus":
            continue
        u = per_user.setdefault(e.username, {"key": e.username,
                                             "display": display.get(e.username, e.username),
                                             "count": 0, "logins": 0, "views": 0,
                                             "actions": 0})
        u["actions"] += 1
        u["count"] += 1
        actions += 1
        day = per_day.setdefault(e.created_at.strftime("%Y-%m-%d"),
                                 {"count": 0, "by_user": {}})
        day["count"] += 1
        day["by_user"][e.username] = day["by_user"].get(e.username, 0) + 1
        per_hour[e.created_at.hour] += 1

    users_out = sorted(per_user.values(), key=lambda x: -x["count"])

    # ---- optional TEAM fold: usernames → inventory teams via LDAP ---------
    teams_out = []
    try:
        from ..integrations import inventory, project_report
        inv = inventory.parse()
        all_teams: dict = {}
        for p in inv.get("projects") or []:
            for t in (p.get("teams") or {}).values():
                if t:
                    all_teams[f"t{len(all_teams)}"] = t
        if all_teams:
            teams_out = project_report._team_fold(
                [{"key": u["key"], "count": u["count"]} for u in users_out],
                {"teams": all_teams})
    except Exception:  # noqa: BLE001 — LDAP/inventory trouble hides the fold
        teams_out = []

    recent = [{"at": r.at.isoformat()[:16].replace("T", " "),
               "username": r.username,
               "display": display.get(r.username, r.username),
               "kind": r.kind, "page": r.page, "detail": r.detail}
              for r in rows[:120]]
    return {"days": days,
            "summary": {"events": len(rows) + actions,
                        "active_users": len(users_out),
                        "logins": logins, "views": views, "actions": actions,
                        "top_page": max(per_page, key=per_page.get) if per_page else None},
            "users": users_out,
            "pages": sorted(({"key": k, "count": v} for k, v in per_page.items()),
                            key=lambda x: -x["count"]),
            "teams": teams_out,
            "per_day": [{"day": d, "count": v["count"], "by_user": v["by_user"]}
                        for d, v in sorted(per_day.items())],
            "per_hour": per_hour,
            "recent": recent}
