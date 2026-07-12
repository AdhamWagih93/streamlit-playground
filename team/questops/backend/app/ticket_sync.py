"""Reconcile Jira-side closures into the gamification ledger.

Tickets closed DIRECTLY in Jira (never transitioned through QuestOps) must
still count toward completed-ticket stats, quests and badges. This sync
backfills a ticket_done XPEvent for any done-status ticket in the closed
window that has no event yet — deduped by issue key, credited to the
assignee, timestamped at the Jira resolution date so windowed views count
it in the right range."""

import datetime as dt
import re
import time

from sqlalchemy.orm import Session

from .db import User, XPEvent, utcnow
from .gamification import (XP_RULES, _check_badges, _check_team_badges,
                           _check_team_quests, _update_streak)
from .integrations import jira

_LAST = {"at": 0.0}
SYNC_TTL = 600  # seconds between real Jira reconciliations


def _parse_when(raw: str | None) -> dt.datetime | None:
    """Jira timestamps ('2026-07-12T10:11:12.000+0300') -> naive UTC."""
    if not raw:
        return None
    try:
        cleaned = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", raw.strip())
        parsed = dt.datetime.fromisoformat(cleaned)
        if parsed.tzinfo:
            parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def sync_closed_tickets(db: Session, force: bool = False) -> int:
    """Returns how many Jira-side closures were backfilled. TTL-cached and
    outage-safe — a Jira hiccup never breaks the calling page."""
    if not force and time.time() - _LAST["at"] < SYNC_TTL:
        return 0
    _LAST["at"] = time.time()
    try:
        closed = jira.closed_recently()
    except Exception:  # noqa: BLE001 — reconcile later
        return 0
    if not closed:
        return 0

    users = {u.username.lower(): u for u in db.query(User).all()}
    already = {ref for (ref,) in db.query(XPEvent.ref).filter(
        XPEvent.kind == "ticket_done",
        XPEvent.ref.in_([c["key"] for c in closed]))}

    added, touched = 0, set()
    for c in closed:
        if c["key"] in already or not c.get("assignee"):
            continue  # closed via QuestOps already counted / nobody to credit
        user = users.get(str(c["assignee"]).lower())
        if user is None:
            continue  # assignee is not a team member
        when = _parse_when(c.get("resolved")) or _parse_when(c.get("updated")) or utcnow()
        points = XP_RULES["ticket_done"]
        db.add(XPEvent(username=user.username, kind="ticket_done", points=points,
                       message=f"(jira) {c['key']} closed in Jira — "
                               f"{(c.get('summary') or '')[:60]}",
                       ref=c["key"], created_at=when))
        user.xp += points
        if when.date() == utcnow().date():
            _update_streak(user)  # same-day closes keep the streak honest
        touched.add(user.username)
        added += 1

    if added:
        db.commit()
        for username in touched:  # backfilled closes can unlock achievements
            _check_badges(db, users[username])
        _check_team_quests(db)
        _check_team_badges(db)
        db.commit()
    return added
