from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.rbac import User
from ..auth.sessions import admin_user
from ..providers import impl

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("/summary")
def summary(user: User = Depends(admin_user)):
    return impl("teams").summary(user)


@router.get("/members/all")
def members_all(q: str = "", team: str = "", page: int = 1, size: int = 50,
                user: User = Depends(admin_user)):
    return impl("teams").members_all(user, q, team, page, size)


@router.get("/{team}")
def team_detail(team: str, user: User = Depends(admin_user)):
    out = impl("teams").team_detail(user, team)
    if out is None:
        raise HTTPException(status_code=404, detail=f"Unknown team '{team}'")
    return out
