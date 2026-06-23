"""Notification routes, scoped to the authenticated user."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Notification, User
from app.schemas.common import Message

router = APIRouter()


class ActorBrief(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    display_name: str


class NotificationOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    verb: str
    message: str
    is_read: bool
    issue_id: int | None = None
    created_at: datetime
    actor: ActorBrief | None = None


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    unread_only: bool = False,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == current.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(50)
    return list(db.scalars(stmt).all())


@router.get("/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, int]:
    stmt = select(Notification).where(
        Notification.user_id == current.id, Notification.is_read.is_(False)
    )
    count = len(db.scalars(stmt).all())
    return {"count": count}


@router.post("/{notif_id}/read", response_model=Message)
def mark_read(
    notif_id: int,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> Message:
    notif = db.get(Notification, notif_id)
    if notif is None or notif.user_id != current.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    notif.is_read = True
    db.add(notif)
    db.commit()
    return Message(detail="Notification marked as read")


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, int]:
    result = db.execute(
        update(Notification)
        .where(Notification.user_id == current.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.commit()
    return {"updated": result.rowcount or 0}
