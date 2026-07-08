from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth.rbac import User
from ..auth.sessions import admin_user
from ..providers import impl

router = APIRouter(prefix="/people", tags=["people"])


@router.get("/summary")
def summary(window: str = "90d", page: int = 1, size: int = 50,
            user: User = Depends(admin_user)):
    return impl("people").summary(user, window, page, size)
