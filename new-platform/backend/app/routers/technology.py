from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth.rbac import User
from ..auth.sessions import admin_user
from ..providers import impl

router = APIRouter(prefix="/technology", tags=["technology"])


@router.get("/summary")
def summary(dim: str = "build_technology", by: str = "team",
            user: User = Depends(admin_user)):
    return impl("technology").summary(user, dim, by)
