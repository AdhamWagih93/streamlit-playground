"""XP, levels, streaks, badges and daily quests.

Every awarded action is an XPEvent row — that table doubles as the
activity history, so 'present and past' views share one truth.
"""

import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import BadgeAward, User, XPEvent, utcnow

XP_RULES = {
    "ticket_done": 40,
    "ticket_resolved": 15,  # moved to review ('Resolved')
    "ticket_progress": 10,
    "ticket_comment": 5,
    "ticket_claimed": 5,
    "build_claimed": 10,
    "build_fixed": 35,
    "approval_review": 15,
    "prompt_created": 10,
    "prompt_refined": 8,
    "repo_action_requested": 10,
    "repo_action_executed": 30,
    "quest_bonus": 0,  # points carried on the event itself
}

RANKS = [
    (1, "Recruit"), (3, "Operator"), (5, "Specialist"), (8, "Sergeant"),
    (12, "Captain"), (16, "Commander"), (20, "Warden"), (25, "Legend"),
]

BADGES = [
    {"key": "first_blood", "name": "First Blood", "icon": "🩸", "kind": "ticket_done", "count": 1,
     "desc": "Close your first ticket"},
    {"key": "closer_25", "name": "The Closer", "icon": "🎯", "kind": "ticket_done", "count": 25,
     "desc": "Close 25 tickets"},
    {"key": "build_medic", "name": "Build Medic", "icon": "⛑️", "kind": "build_fixed", "count": 5,
     "desc": "Fix 5 failing builds"},
    {"key": "firefighter", "name": "Firefighter", "icon": "🧯", "kind": "build_fixed", "count": 15,
     "desc": "Fix 15 failing builds"},
    {"key": "gatekeeper", "name": "Gatekeeper", "icon": "🛡️", "kind": "approval_review", "count": 10,
     "desc": "Review 10 repo actions"},
    {"key": "prompt_smith", "name": "Prompt Smith", "icon": "⚒️", "kind": "prompt_created", "count": 3,
     "desc": "Author 3 prompt templates"},
    {"key": "automator", "name": "Automator", "icon": "🤖", "kind": "repo_action_executed", "count": 5,
     "desc": "Land 5 approved repo actions"},
    {"key": "streak_5", "name": "On Fire", "icon": "🔥", "streak": 5,
     "desc": "5-day activity streak"},
    {"key": "streak_15", "name": "Unstoppable", "icon": "☄️", "streak": 15,
     "desc": "15-day activity streak"},
]

DAILY_QUESTS = [
    {"key": "clear_two", "name": "Clear the Deck", "desc": "Get 2 tickets closed today",
     "kind": "ticket_done", "target": 2, "bonus": 25},
    {"key": "medic", "name": "Build Medic", "desc": "Fix a failing build today",
     "kind": "build_fixed", "target": 1, "bonus": 25},
    {"key": "gate", "name": "Gatekeeper", "desc": "Review a pending repo action today",
     "kind": "approval_review", "target": 1, "bonus": 20},
]


def xp_for_level(level: int) -> int:
    if level <= 1:
        return 0
    return int(120 * (level - 1) ** 1.6)


def level_for_xp(xp: int) -> int:
    level = 1
    while xp >= xp_for_level(level + 1):
        level += 1
    return level


def rank_for_level(level: int) -> str:
    name = RANKS[0][1]
    for lvl, rank in RANKS:
        if level >= lvl:
            name = rank
    return name


def level_info(xp: int) -> dict:
    level = level_for_xp(xp)
    cur, nxt = xp_for_level(level), xp_for_level(level + 1)
    return {
        "level": level,
        "rank": rank_for_level(level),
        "xp": xp,
        "level_floor": cur,
        "next_level_xp": nxt,
        "progress": round((xp - cur) / max(nxt - cur, 1), 3),
    }


def _today() -> dt.date:
    return utcnow().date()


def _count_kind(db: Session, username: str, kind: str, since: dt.datetime | None = None) -> int:
    q = db.query(func.count(XPEvent.id)).filter(
        XPEvent.username == username, XPEvent.kind == kind)
    if since is not None:
        q = q.filter(XPEvent.created_at >= since)
    return q.scalar() or 0


def _update_streak(user: User) -> None:
    today = _today().isoformat()
    if user.last_active == today:
        return
    yesterday = (_today() - dt.timedelta(days=1)).isoformat()
    user.streak = user.streak + 1 if user.last_active == yesterday else 1
    user.last_active = today


def _check_badges(db: Session, user: User) -> list[dict]:
    owned = {b.key for b in db.query(BadgeAward).filter(BadgeAward.username == user.username)}
    new = []
    for badge in BADGES:
        if badge["key"] in owned:
            continue
        earned = False
        if "kind" in badge:
            earned = _count_kind(db, user.username, badge["kind"]) >= badge["count"]
        elif "streak" in badge:
            earned = user.streak >= badge["streak"]
        if earned:
            db.add(BadgeAward(username=user.username, key=badge["key"],
                              name=badge["name"], icon=badge["icon"]))
            new.append({"key": badge["key"], "name": badge["name"], "icon": badge["icon"]})
    return new


def quest_progress(db: Session, username: str) -> list[dict]:
    day_start = dt.datetime.combine(_today(), dt.time.min)
    out = []
    for q in DAILY_QUESTS:
        done = _count_kind(db, username, q["kind"], since=day_start)
        out.append({**q, "progress": min(done, q["target"]),
                    "complete": done >= q["target"]})
    return out


def _check_quests(db: Session, user: User) -> list[dict]:
    """Grant the daily bonus once per quest per day."""
    day = _today().isoformat()
    completed = []
    for q in quest_progress(db, user.username):
        if not q["complete"]:
            continue
        ref = f"quest:{q['key']}:{day}"
        already = db.query(XPEvent).filter(
            XPEvent.username == user.username, XPEvent.ref == ref).first()
        if already:
            continue
        db.add(XPEvent(username=user.username, kind="quest_bonus", points=q["bonus"],
                       message=f"Daily quest complete: {q['name']}", ref=ref))
        user.xp += q["bonus"]
        completed.append({"name": q["name"], "bonus": q["bonus"]})
    return completed


def award(db: Session, user: User, kind: str, message: str = "", ref: str = "") -> dict:
    """Record an action, update XP/streak/badges/quests.

    Returns a 'game' payload the frontend turns into toasts.
    """
    points = XP_RULES.get(kind, 0)
    level_before = level_for_xp(user.xp)

    db.add(XPEvent(username=user.username, kind=kind, points=points,
                   message=message, ref=ref))
    user.xp += points
    _update_streak(user)
    quests = _check_quests(db, user)
    badges = _check_badges(db, user)
    db.commit()

    info = level_info(user.xp)
    return {
        "points": points,
        "message": message,
        "level_up": info["level"] if info["level"] > level_before else None,
        "new_badges": badges,
        "quests_completed": quests,
        "level": info,
        "streak": user.streak,
    }
