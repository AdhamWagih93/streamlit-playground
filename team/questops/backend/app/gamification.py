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
    "ticket_objective": 5,  # tagged a ticket with a team objective
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

# counted across the WHOLE team; completing one pays every member
TEAM_QUESTS = [
    {"key": "team_five", "name": "Clear Five", "desc": "Close 5 tickets as a team today",
     "kind": "ticket_done", "target": 5, "bonus": 15},
    {"key": "team_green", "name": "Green Machine", "desc": "Fix 2 builds as a team today",
     "kind": "build_fixed", "target": 2, "bonus": 15},
    {"key": "team_ship", "name": "Ship It", "desc": "Land 3 approved repo actions today",
     "kind": "repo_action_executed", "target": 3, "bonus": 10},
]

# awarded to every member when the team earns them
TEAM_BADGES = [
    {"key": "full_squad", "name": "Full Squad", "icon": "🤝", "team": True,
     "desc": "Every member earns XP on the same day"},
    {"key": "powerhouse", "name": "Powerhouse", "icon": "⚡", "team": True,
     "desc": "500 team XP in a single day"},
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


def team_quest_progress(db: Session) -> list[dict]:
    day_start = dt.datetime.combine(_today(), dt.time.min)
    out = []
    for q in TEAM_QUESTS:
        done = (db.query(func.count(XPEvent.id))
                .filter(XPEvent.kind == q["kind"], XPEvent.created_at >= day_start)
                .scalar() or 0)
        out.append({**q, "progress": min(done, q["target"]),
                    "complete": done >= q["target"], "team": True})
    return out


def _check_team_quests(db: Session) -> list[dict]:
    """Completing a team quest pays the bonus to EVERY member, once per day."""
    day = _today().isoformat()
    users = db.query(User).all()
    completed = []
    for q in team_quest_progress(db):
        if not q["complete"]:
            continue
        ref = f"teamquest:{q['key']}:{day}"
        if db.query(XPEvent).filter(XPEvent.ref == ref).first():
            continue
        for u in users:
            db.add(XPEvent(username=u.username, kind="quest_bonus", points=q["bonus"],
                           message=f"Team quest complete: {q['name']}", ref=ref))
            u.xp += q["bonus"]
        completed.append({"name": q["name"], "bonus": q["bonus"]})
    return completed


def _check_team_badges(db: Session) -> list[dict]:
    """Team badges land on every member's wall the moment the team earns them."""
    users = db.query(User).all()
    if not users:
        return []
    day_start = dt.datetime.combine(_today(), dt.time.min)
    rows = (db.query(XPEvent.username, func.coalesce(func.sum(XPEvent.points), 0))
            .filter(XPEvent.created_at >= day_start)
            .group_by(XPEvent.username).all())
    active = {r[0] for r in rows}
    day_xp = sum(r[1] for r in rows)

    earned = []
    if all(u.username in active for u in users):
        earned.append(TEAM_BADGES[0])
    if day_xp >= 500:
        earned.append(TEAM_BADGES[1])

    new = []
    for badge in earned:
        holders = {b.username for b in
                   db.query(BadgeAward).filter(BadgeAward.key == badge["key"])}
        missing = [u for u in users if u.username not in holders]
        if not missing:
            continue
        for u in missing:
            db.add(BadgeAward(username=u.username, key=badge["key"],
                              name=badge["name"], icon=badge["icon"]))
        new.append({"key": badge["key"], "name": badge["name"], "icon": badge["icon"]})
    return new


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
    team_quests = _check_team_quests(db)
    badges = _check_badges(db, user)
    team_badges = _check_team_badges(db)
    db.commit()

    info = level_info(user.xp)
    return {
        "points": points,
        "message": message,
        "level_up": info["level"] if info["level"] > level_before else None,
        "new_badges": badges + team_badges,
        "quests_completed": quests,
        "team_quests_completed": team_quests,
        "level": info,
        "streak": user.streak,
    }
