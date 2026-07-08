from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth.rbac import User
from ..auth.sessions import current_user
from ..providers import impl

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
def list_events(
    window: str = "7d",
    types: str = "",
    envs: str = "",
    q: str = "",
    user_q: str = Query("", alias="user"),
    page: int = 1,
    size: int = 75,
    user: User = Depends(current_user),
):
    return impl("events").list_events(user, window, types, envs, q, user_q, page, size)


@router.get("/types")
def event_types(user: User = Depends(current_user)):
    return impl("events").event_types(user)
