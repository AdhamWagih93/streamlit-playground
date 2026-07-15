"""Access Management endpoints — each source loads independently (lazy),
everything cached in the integration layer so refreshes are explicit."""

from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..db import User
from ..integrations import access

router = APIRouter(prefix="/api/access", tags=["access"])


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — one broken source, not a broken page
        raise HTTPException(502, str(exc)[:250])


@router.get("/ado")
def ado(refresh: bool = False, user: User = Depends(current_user)):
    return _wrap(access.ado_projects, refresh)


@router.get("/ado/{project_id}")
def ado_project(project_id: str, refresh: bool = False,
                user: User = Depends(current_user)):
    return _wrap(access.ado_project_access, project_id, refresh)


@router.get("/jira")
def jira_schemes(refresh: bool = False, user: User = Depends(current_user)):
    return _wrap(access.jira_permission_schemes, refresh)


@router.get("/jenkins")
def jenkins(refresh: bool = False, user: User = Depends(current_user)):
    return _wrap(access.jenkins_matrix, refresh)
