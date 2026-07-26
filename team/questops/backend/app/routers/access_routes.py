"""Access Management endpoints — each source loads independently (lazy),
everything cached in the integration layer so refreshes are explicit."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import current_user, require_approver
from ..db import User, get_db
from ..integrations import access, migration

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
    """Live health probe: run the Engine repo's getTeamMembersCN.sh <team> and
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


@router.get("/jira/activity")
def jira_activity(refresh: bool = False, user: User = Depends(current_user)):
    """Per-project last-opened/last-interaction + per-user last-login/activity."""
    return _wrap(access.jira_activity, refresh)


@router.get("/jenkins")
def jenkins(refresh: bool = False, user: User = Depends(current_user)):
    return _wrap(access.jenkins_matrix, refresh)


# ===================================================== ADO -> Gitea migration
@router.get("/migration/targets")
def migration_targets(user: User = Depends(current_user)):
    ado = _wrap(access.ado_projects, False)
    return {"targets": [migration.target_public(t) for t in migration.targets()],
            "collections": ado.get("collections", [])}


class TargetBody(BaseModel):
    collection: str
    url: str
    token: str = ""
    org_strategy: str = "project"


@router.post("/migration/targets")
def migration_add_target(body: TargetBody, user: User = Depends(require_approver),
                         db: Session = Depends(get_db)):
    try:
        out = migration.add_target(db, body.collection, body.url, body.token,
                                   body.org_strategy, user.username)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    migration.invalidate()
    return out


@router.delete("/migration/targets/{target_id}")
def migration_remove_target(target_id: int, user: User = Depends(require_approver),
                            db: Session = Depends(get_db)):
    migration.remove_target(db, target_id)
    migration.invalidate()
    return {"removed": target_id}


@router.get("/migration/plan")
def migration_plan(refresh: bool = False, user: User = Depends(current_user)):
    return _wrap(migration.plan, refresh)


class ExecuteBody(BaseModel):
    collection: str | None = None
    dry_run: bool = True
    confirm: bool = False


@router.post("/migration/execute")
def migration_execute(body: ExecuteBody, user: User = Depends(require_approver)):
    # a real (non-dry-run) migration writes to an external system — require an
    # explicit confirm on top of the approver role
    if not body.dry_run and not body.confirm:
        raise HTTPException(400, "a live migration requires confirm=true")
    return _wrap(migration.execute, body.collection, body.dry_run)
