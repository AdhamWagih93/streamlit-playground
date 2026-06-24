"""Canonical notification dispatch layer (in-app + email, honoring prefs).

Every notifiable event funnels through :func:`dispatch`, which consults the
recipient's per-channel :class:`UserNotificationPreference` rows. When no row
exists the :data:`DEFAULTS` map decides. In-app delivery writes a
:class:`Notification`; email delivery (only when instance mail is enabled) goes
through :mod:`app.services.mail`.

``mail`` is imported lazily inside :func:`dispatch` to avoid import cycles, and
every delivery path is wrapped so a notification failure never breaks the
request that triggered it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NOTIFICATION_EVENTS, MailConfig, User
from app.models.activity import Notification
from app.models.notify_prefs import UserNotificationPreference

logger = logging.getLogger(__name__)

# Per-(event, channel) default when the user has no explicit preference row.
# Most events default to in-app on, email off; the two highest-signal events
# (direct assignment / @mention) default to email on as well.
_EMAIL_ON_BY_DEFAULT = {"issue_assigned", "issue_mentioned"}


def _default(event: str, channel: str) -> bool:
    if channel == "in_app":
        return True
    if channel == "email":
        return event in _EMAIL_ON_BY_DEFAULT
    return False


# Materialized default map (event -> channel -> bool) for convenience.
DEFAULTS: dict[str, dict[str, bool]] = {
    event: {"in_app": _default(event, "in_app"), "email": _default(event, "email")}
    for event in NOTIFICATION_EVENTS
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_preference(db: Session, user_id: int, event: str, channel: str) -> bool:
    """Effective preference for (user, event, channel), falling back to defaults."""
    row = db.scalars(
        select(UserNotificationPreference).where(
            UserNotificationPreference.user_id == user_id,
            UserNotificationPreference.event == event,
            UserNotificationPreference.channel == channel,
        )
    ).first()
    if row is None:
        return _default(event, channel)
    return bool(row.enabled)


def ensure_default_preferences(db: Session, user: User) -> None:
    """Create default rows for every event x channel if the user has none.

    Idempotent: only fills in rows that are missing, never overwrites.
    """
    from app.models.notify_prefs import CHANNELS

    existing = db.execute(
        select(UserNotificationPreference.event, UserNotificationPreference.channel)
        .where(UserNotificationPreference.user_id == user.id)
    ).all()
    have = {(row[0], row[1]) for row in existing}
    added = False
    for event in NOTIFICATION_EVENTS:
        for channel in CHANNELS:
            if (event, channel) in have:
                continue
            db.add(
                UserNotificationPreference(
                    user_id=user.id,
                    event=event,
                    channel=channel,
                    enabled=_default(event, channel),
                )
            )
            added = True
    if added:
        db.commit()


def _mail_enabled(db: Session) -> bool:
    cfg = db.get(MailConfig, 1)
    return bool(cfg and cfg.enabled and cfg.host)


def dispatch(
    db: Session,
    user_id: int | None,
    actor_id: int | None,
    issue,
    event: str,
    message: str,
    subject: str | None = None,
    email_body: str | None = None,
) -> None:
    """Deliver a notification for *event* to *user_id* honoring their prefs.

    Writes an in-app :class:`Notification` when the in-app pref is on, and sends
    an email when the email pref is on *and* instance mail is enabled. Self-
    notifications (``user_id == actor_id``) and missing recipients are skipped.
    Never raises.
    """
    if not user_id or user_id == actor_id:
        return
    try:
        if get_preference(db, user_id, event, "in_app"):
            db.add(
                Notification(
                    user_id=user_id,
                    actor_id=actor_id,
                    issue_id=getattr(issue, "id", None),
                    verb=event,
                    message=message,
                    created_at=_now(),
                )
            )
            db.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("In-app notification failed for user %s: %s", user_id, exc)

    try:
        if not get_preference(db, user_id, event, "email"):
            return
        if not _mail_enabled(db):
            return
        recipient = db.get(User, user_id)
        if recipient is None or not recipient.email:
            return

        issue_key = getattr(issue, "key", None)
        subj = subject or (f"[{issue_key}] {message}" if issue_key else message)
        body = email_body or message
        if issue_key:
            body = f"{body}\n\nIssue: {issue_key}"

        from app.services import mail  # lazy import to avoid cycles

        ok, detail = mail.send_email(db, recipient.email, subj, body)
        if not ok:
            logger.info("Email notification not sent to %s: %s", recipient.email, detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Email notification failed for user %s: %s", user_id, exc)
