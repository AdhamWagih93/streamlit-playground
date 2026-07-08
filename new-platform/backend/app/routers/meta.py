from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth.rbac import User
from ..auth.sessions import admin_user
from ..providers import impl

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/integrations")
def integrations(user: User = Depends(admin_user)):
    return impl("meta").integrations(user)


@router.get("/glossary")
def glossary(user: User = Depends(admin_user)):
    return impl("meta").glossary(user)
