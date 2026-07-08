from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from ..auth.rbac import User
from ..auth.sessions import current_user
from ..providers import impl

router = APIRouter(prefix="/security", tags=["security"])


@router.get("/summary")
def summary(
    scanner: str = "all",
    q: str = "",
    project: str = "",
    only_findings: bool = False,
    severity_floor: str = "low",
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    user: User = Depends(current_user),
):
    return impl("security").summary(
        user, scanner=scanner, q=q, project=project, only_findings=only_findings,
        severity_floor=severity_floor, page=page, size=size,
    )


@router.get("/app/{project}/{application}")
def app_detail(project: str, application: str, user: User = Depends(current_user)):
    return impl("security").app_detail(user, project, application)


@router.get("/report/{scanner}/{project}/{application}/{version}", response_class=HTMLResponse)
def report(scanner: str, project: str, application: str, version: str,
           user: User = Depends(current_user)):
    return HTMLResponse(impl("security").report(user, scanner, project, application, version))
