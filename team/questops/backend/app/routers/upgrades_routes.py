"""Upgrade checker tab: current vs latest LTS per integrated tool."""

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..db import User
from ..integrations import upgrades

router = APIRouter(prefix="/api", tags=["upgrades"])


@router.get("/upgrades")
def check(refresh: bool = False, user: User = Depends(current_user)):
    return upgrades.check(force=refresh)
