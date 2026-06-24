"""Per-user notification preference routes (mounted at /notification-preferences).

All routes are scoped to the authenticated user; there is no cross-user access.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import NOTIFICATION_EVENTS, MailConfig, User
from app.models.notify_prefs import CHANNELS, UserNotificationPreference
from app.schemas.notify import (
    NotificationPreferences,
    PreferenceRow,
    PreferencesUpdate,
)
from app.services import notifications as notify_svc

router = APIRouter()


def _mail_available(db: Session) -> bool:
    cfg = db.get(MailConfig, 1)
    return bool(cfg and cfg.enabled)


def _build_response(db: Session, user: User) -> NotificationPreferences:
    rows = db.scalars(
        select(UserNotificationPreference).where(
            UserNotificationPreference.user_id == user.id
        )
    ).all()
    by_key = {(r.event, r.channel): bool(r.enabled) for r in rows}

    pref_rows: list[PreferenceRow] = []
    for event, label in NOTIFICATION_EVENTS.items():
        pref_rows.append(
            PreferenceRow(
                event=event,
                label=label,
                in_app=by_key.get(
                    (event, "in_app"), notify_svc.get_preference(db, user.id, event, "in_app")
                ),
                email=by_key.get(
                    (event, "email"), notify_svc.get_preference(db, user.id, event, "email")
                ),
            )
        )
    return NotificationPreferences(
        email_available=_mail_available(db),
        rows=pref_rows,
    )


@router.get("", response_model=NotificationPreferences)
def get_preferences(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationPreferences:
    notify_svc.ensure_default_preferences(db, user)
    return _build_response(db, user)


@router.put("", response_model=NotificationPreferences)
def update_preferences(
    payload: PreferencesUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationPreferences:
    for upd in payload.updates:
        if upd.event not in NOTIFICATION_EVENTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown event: {upd.event}",
            )
        if upd.channel not in CHANNELS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown channel: {upd.channel}",
            )
        row = db.scalars(
            select(UserNotificationPreference).where(
                UserNotificationPreference.user_id == user.id,
                UserNotificationPreference.event == upd.event,
                UserNotificationPreference.channel == upd.channel,
            )
        ).first()
        if row is None:
            db.add(
                UserNotificationPreference(
                    user_id=user.id,
                    event=upd.event,
                    channel=upd.channel,
                    enabled=upd.enabled,
                )
            )
        else:
            row.enabled = upd.enabled
    db.commit()
    return _build_response(db, user)
