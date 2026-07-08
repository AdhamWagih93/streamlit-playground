from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.rbac import User
from ..auth.sessions import current_user
from ..providers import impl

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("")
def list_inventory(q: str = "", projects: str = "", company: str = "",
                   app_type: str = "", technology: str = "", platform: str = "",
                   sort: str = "name", page: int = 1, size: int = 50,
                   user: User = Depends(current_user)):
    return impl("inventory").list_inventory(
        user, q=q, projects=projects, company=company, app_type=app_type,
        technology=technology, platform=platform, sort=sort, page=page, size=size,
    )


@router.get("/facets")
def facets(user: User = Depends(current_user)):
    return impl("inventory").facets(user)


@router.get("/app/{project}/{application}")
def app_detail(project: str, application: str, user: User = Depends(current_user)):
    detail = impl("inventory").app_detail(user, project, application)
    if detail is None:
        # 404 for both "doesn't exist" and "not visible" — no existence leaks.
        raise HTTPException(status_code=404, detail="Application not found")
    return detail
