"""E-mail page — read-only view of the service account mailbox (EWS)."""

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..db import User
from ..integrations import mailbox

router = APIRouter(prefix="/api/mail", tags=["mail"])


@router.get("")
def mail_list(folder: str = "inbox", q: str = "", sender: str = "", unread: bool = False,
              attachments: bool = False, no_bounces: bool = False, admin_only: bool = False,
              inc_s: str = "", exc_s: str = "", inc_t: str = "", exc_t: str = "",
              days: int = mailbox.MAX_DAYS, limit: int = 50,
              offset: int = 0, refresh: bool = False, user: User = Depends(current_user)):
    return mailbox.list_messages(folder=folder, q=q, sender=sender, unread=unread,
                                 attachments=attachments, no_bounces=no_bounces, admin_only=admin_only,
                                 inc_s=inc_s, exc_s=exc_s, inc_t=inc_t, exc_t=exc_t,
                                 days=days, limit=limit, offset=offset, refresh=refresh)


@router.get("/message")
def mail_message(id: str, user: User = Depends(current_user)):
    return mailbox.get_message(id)
