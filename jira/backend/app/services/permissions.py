"""Central authorization engine.

Evaluates global and project permissions against a user's groups, project roles
and the special dynamic holders (reporter/assignee/project lead/anyone). Site
administrators (``User.is_admin``) bypass every check. This module is the single
source of truth for "can user X do Y" decisions across the API.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import (
    GlobalPermissionGrant,
    Group,
    PermissionScheme,
    Project,
    ProjectRole,
    ProjectRoleActor,
    User,
    user_groups,
)
from app.models.issue import Issue
from app.services import permission_keys as P


# --- Group / role resolution ----------------------------------------------
def user_group_ids(db: Session, user: User) -> set[int]:
    return set(db.scalars(select(user_groups.c.group_id).where(user_groups.c.user_id == user.id)))


def user_group_names(db: Session, user: User) -> set[str]:
    return set(
        db.scalars(
            select(Group.name)
            .join(user_groups, user_groups.c.group_id == Group.id)
            .where(user_groups.c.user_id == user.id)
        )
    )


def user_project_role_ids(db: Session, user: User, project: Project) -> set[int]:
    gids = user_group_ids(db, user)
    conds = [ProjectRoleActor.user_id == user.id]
    if gids:
        conds.append(ProjectRoleActor.group_id.in_(gids))
    q = select(ProjectRoleActor.role_id).where(
        ProjectRoleActor.project_id == project.id, or_(*conds)
    )
    return set(db.scalars(q))


# --- Global permissions ----------------------------------------------------
def is_site_admin(db: Session, user: User) -> bool:
    if user.is_admin:
        return True
    return has_global_permission(db, user, P.ADMINISTER)


def has_global_permission(db: Session, user: User, permission: str) -> bool:
    if user.is_admin:
        return True
    gnames = user_group_names(db, user)
    grants = db.scalars(
        select(GlobalPermissionGrant).where(GlobalPermissionGrant.permission == permission)
    )
    for g in grants:
        if g.holder_type == P.HOLDER_USER and g.holder_value == str(user.id):
            return True
        if g.holder_type == P.HOLDER_GROUP and g.holder_value in gnames:
            return True
    return False


# --- Project permissions ---------------------------------------------------
def get_effective_scheme(db: Session, project: Project) -> PermissionScheme | None:
    if project.permission_scheme_id:
        scheme = db.get(PermissionScheme, project.permission_scheme_id)
        if scheme:
            return scheme
    return db.scalars(
        select(PermissionScheme).where(PermissionScheme.is_default.is_(True)).limit(1)
    ).first()


def _grant_matches(
    db: Session,
    grant,
    user: User,
    project: Project,
    issue: Issue | None,
    role_ids: set[int],
    gnames: set[str],
) -> bool:
    ht, hv = grant.holder_type, grant.holder_value
    if ht == P.HOLDER_SPECIAL:
        if hv == P.SPECIAL_ANYONE:
            return True
        if hv == P.SPECIAL_CURRENT_USER:
            return True  # any authenticated user
        if hv == P.SPECIAL_PROJECT_LEAD:
            return project.lead_id == user.id
        if hv == P.SPECIAL_REPORTER:
            return issue is not None and issue.reporter_id == user.id
        if hv == P.SPECIAL_ASSIGNEE:
            return issue is not None and issue.assignee_id == user.id
        return False
    if ht == P.HOLDER_GROUP:
        return hv in gnames
    if ht == P.HOLDER_USER:
        return hv == str(user.id) or (user.external_id and hv == user.external_id)
    if ht == P.HOLDER_PROJECT_ROLE:
        # holder_value may be a role id or a role name.
        if hv and hv.isdigit():
            return int(hv) in role_ids
        role = db.scalars(select(ProjectRole).where(ProjectRole.name == hv)).first()
        return bool(role and role.id in role_ids)
    return False


def has_project_permission(
    db: Session, user: User, project: Project, permission: str, issue: Issue | None = None
) -> bool:
    if is_site_admin(db, user):
        return True
    scheme = get_effective_scheme(db, project)
    if scheme is None:
        return False
    role_ids = user_project_role_ids(db, user, project)
    gnames = user_group_names(db, user)
    for grant in scheme.grants:
        if grant.permission != permission:
            continue
        if _grant_matches(db, grant, user, project, issue, role_ids, gnames):
            return True
    return False


def visible_project_ids(db: Session, user: User) -> set[int] | None:
    """Return the set of project ids the user may BROWSE, or None for 'all'
    (site admins). Callers use None to skip filtering entirely.
    """
    if is_site_admin(db, user):
        return None
    ids: set[int] = set()
    for project in db.scalars(select(Project)):
        if has_project_permission(db, user, project, P.BROWSE_PROJECTS):
            ids.add(project.id)
    return ids


# --- Project default wiring (used on project creation / sync) --------------
def setup_project_defaults(db: Session, project: Project, lead: User | None) -> None:
    """Assign the default permission scheme and make *lead* a project admin."""
    if not project.permission_scheme_id:
        default = db.scalars(
            select(PermissionScheme).where(PermissionScheme.is_default.is_(True)).limit(1)
        ).first()
        if default:
            project.permission_scheme_id = default.id
    if lead:
        admin_role = db.scalars(
            select(ProjectRole).where(ProjectRole.name == "Administrators")
        ).first()
        if admin_role:
            exists = db.scalars(
                select(ProjectRoleActor).where(
                    ProjectRoleActor.project_id == project.id,
                    ProjectRoleActor.role_id == admin_role.id,
                    ProjectRoleActor.user_id == lead.id,
                )
            ).first()
            if not exists:
                db.add(ProjectRoleActor(project_id=project.id, role_id=admin_role.id, user_id=lead.id))
    db.flush()


# --- FastAPI dependencies --------------------------------------------------
def require_site_admin(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> User:
    if not is_site_admin(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Site administrator privileges required",
        )
    return user


def require_global_permission(permission: str):
    def _dep(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if not has_global_permission(db, user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {permission}",
            )
        return user

    return _dep


def assert_project_permission(
    db: Session, user: User, project: Project, permission: str, issue: Issue | None = None
) -> None:
    """Raise 403 unless *user* holds *permission* on *project*. Default-deny."""
    if not has_project_permission(db, user, project, permission, issue):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing project permission: {permission}",
        )


def assert_own_or_all(
    db: Session,
    user: User,
    project: Project,
    owner_id: int | None,
    own_permission: str,
    all_permission: str,
    issue: Issue | None = None,
) -> None:
    """Allow when the user may act on *anyone's* item (all_permission) OR owns
    the item and may act on their *own* (own_permission). Otherwise 403.

    Used for comments / worklogs / attachments where Jira distinguishes
    edit/delete-own from edit/delete-all.
    """
    if has_project_permission(db, user, project, all_permission, issue):
        return
    if owner_id is not None and owner_id == user.id and has_project_permission(
        db, user, project, own_permission, issue
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Missing project permission: {all_permission} (or {own_permission} on your own item)",
    )
