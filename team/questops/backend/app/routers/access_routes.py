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


@router.get("/summary")
def summary(refresh: bool = False, user: User = Depends(current_user)):
    return _wrap(access.access_summary, refresh)


@router.get("/ldap")
def ldap(refresh: bool = False, user: User = Depends(current_user)):
    return _wrap(access.ldap_health, refresh)


@router.get("/ldap/test")
def ldap_test(team: str = "", user: User = Depends(current_user)):
    """Live health probe: run the Engine repo's getTeamMembers.sh <team> and
    return its raw output + parsed members (used from the Access page)."""
    from ..auth import probe_team_resolver
    return _wrap(probe_team_resolver, team)


@router.get("/ado")
def ado(refresh: bool = False, user: User = Depends(current_user)):
    return _wrap(access.ado_projects, refresh)


@router.get("/ado/{collection}/{project_id}")
def ado_project(collection: str, project_id: str, refresh: bool = False,
                user: User = Depends(current_user)):
    return _wrap(access.ado_project_access, collection, project_id, refresh)


@router.get("/jira")
def jira_schemes(refresh: bool = False, user: User = Depends(current_user)):
    return _wrap(access.jira_permission_schemes, refresh)


@router.get("/jenkins")
def jenkins(refresh: bool = False, user: User = Depends(current_user)):
    return _wrap(access.jenkins_matrix, refresh)
