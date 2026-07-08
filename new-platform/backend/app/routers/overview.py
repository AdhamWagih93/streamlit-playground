from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth.rbac import User
from ..auth.sessions import current_user
from ..providers import impl

router = APIRouter(prefix="/overview", tags=["overview"])


@router.get("/summary")
def summary(user: User = Depends(current_user)):
    return impl("overview").summary(user)


@router.get("/events")
def recent_events(limit: int = 30, user: User = Depends(current_user)):
    return impl("overview").recent_events(user, min(limit, 100))
