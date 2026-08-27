"""Projects page — per-project drill-down across every connected system."""

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..db import User
from ..integrations import project_report

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def projects_list(user: User = Depends(current_user)):
    return project_report.list_projects()


@router.get("/{name}")
def project_detail(name: str, days: int = 30, refresh: bool = False,
                   user: User = Depends(current_user)):
    if refresh:
        project_report.invalidate()
    return project_report.report(name, days=days, refresh=refresh)
