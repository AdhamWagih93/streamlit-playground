"""Per-user notification preferences (the user's own notification scheme)."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# Catalogue of notifiable events. Each user can toggle in-app and email
# delivery per event. Keys are stable identifiers used across the app.
NOTIFICATION_EVENTS = {
    "issue_assigned": "An issue is assigned to me",
    "issue_created": "An issue is created in a project I'm in",
    "issue_updated": "An issue I report/am assigned/watch is updated",
    "issue_commented": "Someone comments on an issue I'm involved in",
    "issue_mentioned": "Someone @mentions me",
    "issue_status_changed": "The status of an issue I'm involved in changes",
    "issue_resolved": "An issue I'm involved in is resolved",
    "sprint_started": "A sprint starts on a board I follow",
    "sprint_completed": "A sprint completes on a board I follow",
}

CHANNELS = ("in_app", "email")


class UserNotificationPreference(Base):
    __tablename__ = "user_notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "event", "channel", name="uq_user_notif_pref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # in_app | email
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user = relationship("User")
