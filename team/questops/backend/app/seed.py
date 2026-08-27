"""Demo seed: team of 4 (1 leader/approver + 3 members) with 3 weeks of
deterministic history."""

import datetime as dt
import random

from sqlalchemy.orm import Session

from .auth import DEMO_USERS
from .db import (AgentCommand, BadgeAward, Repository, User, XPEvent,
                 utcnow)
from .gamification import BADGES, _check_badges, level_for_xp

SEED_KINDS = [
    ("ticket_done", 40), ("ticket_progress", 10), ("ticket_comment", 5),
    ("build_fixed", 35),
]



def cleanup_demo_data(db: Session) -> None:
    """Live mode must never show leftovers from an earlier demo run.
    Demo rows carry unambiguous markers: @demo.local emails, '(seeded)'
    event messages, git.example.local repo URLs."""
    demo_users = [u.username for u in
                  db.query(User).filter(User.email.like("%@demo.local"))]
    if demo_users:
        db.query(AgentCommand).filter(AgentCommand.username.in_(demo_users)).delete(
            synchronize_session=False)
        db.query(XPEvent).filter(XPEvent.username.in_(demo_users)).delete(
            synchronize_session=False)
        db.query(BadgeAward).filter(BadgeAward.username.in_(demo_users)).delete(
            synchronize_session=False)
        db.query(User).filter(User.username.in_(demo_users)).delete(
            synchronize_session=False)
    db.query(XPEvent).filter(XPEvent.message.like("(seeded)%")).delete(
        synchronize_session=False)
    db.query(Repository).filter(Repository.url.like("%git.example.local%")).delete(
        synchronize_session=False)
    db.commit()


def seed_demo(db: Session) -> None:
    # demo repositories (normally defined from the UI) — idempotent
    if db.query(Repository).count() == 0:
        for name in ("payments-service", "platform-helm", "Engine"):
            db.add(Repository(name=name, added_by="alice",
                              url=f"https://git.example.local/platform/{name}.git"))
        db.commit()

    if db.query(User).count() > 0:
        return

    from .auth import role_for

    rng = random.Random(42)
    users = []
    for username, meta in DEMO_USERS.items():
        u = User(username=username, display_name=meta["display_name"],
                 email=meta["email"], role=role_for(username), xp=0)
        db.add(u)
        users.append(u)
    db.flush()

    # 21 days of plausible history — leader reviews more, members close more
    now = utcnow()
    for day in range(21, 0, -1):
        date = now - dt.timedelta(days=day)
        if date.weekday() >= 5:
            continue
        for u in users:
            for _ in range(rng.randint(1, 4)):
                kind, points = rng.choice(SEED_KINDS)
                db.add(XPEvent(
                    username=u.username, kind=kind, points=points,
                    message=f"(seeded) {kind.replace('_', ' ')}",
                    created_at=date + dt.timedelta(hours=rng.randint(8, 17))))
                u.xp += points
        db.flush()

    for u in users:
        u.streak = rng.randint(2, 9)
        u.last_active = (now - dt.timedelta(days=1)).date().isoformat()
        _check_badges(db, u)


    db.commit()
