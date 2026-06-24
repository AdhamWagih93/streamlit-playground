"""Seed default RBAC: project roles, the default permission scheme, the system
administrators group and the global ADMINISTER grant.

Designed to be idempotent and Jira-compatible so an imported Jira permission
scheme slots in alongside these defaults.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    GlobalPermissionGrant,
    Group,
    PermissionGrant,
    PermissionScheme,
    ProjectRole,
    User,
)
from app.models.rbac import user_groups
from app.services import permission_keys as P

log = logging.getLogger("trackly.bootstrap")

SYSTEM_ADMIN_GROUP = "trackly-administrators"

DEFAULT_ROLES = [
    ("Administrators", "Full administrative control over the project", False),
    ("Developers", "Work on issues: create, edit, transition, comment", True),
    ("Viewers", "Read-only access to the project", False),
]

# Permissions each default role receives in the default scheme.
_DEV_PERMS = [
    P.BROWSE_PROJECTS, P.CREATE_ISSUES, P.EDIT_ISSUES, P.ASSIGN_ISSUES, P.ASSIGNABLE_USER,
    P.TRANSITION_ISSUES, P.RESOLVE_ISSUES, P.CLOSE_ISSUES, P.LINK_ISSUES, P.SCHEDULE_ISSUES,
    P.MOVE_ISSUES, P.ADD_COMMENTS, P.EDIT_OWN_COMMENTS, P.DELETE_OWN_COMMENTS,
    P.CREATE_ATTACHMENTS, P.DELETE_OWN_ATTACHMENTS, P.WORK_ON_ISSUES, P.EDIT_OWN_WORKLOGS,
    P.DELETE_OWN_WORKLOGS, P.MANAGE_SPRINTS, P.VIEW_VOTERS_AND_WATCHERS, P.MANAGE_WATCHERS,
]
_VIEWER_PERMS = [P.BROWSE_PROJECTS, P.VIEW_VOTERS_AND_WATCHERS]


def seed_roles(db: Session) -> dict[str, ProjectRole]:
    roles: dict[str, ProjectRole] = {}
    for name, desc, is_default in DEFAULT_ROLES:
        role = db.scalars(select(ProjectRole).where(ProjectRole.name == name)).first()
        if not role:
            role = ProjectRole(name=name, description=desc, is_default=is_default)
            db.add(role)
            db.flush()
        roles[name] = role
    return roles


def seed_default_scheme(db: Session) -> None:
    scheme = db.scalars(select(PermissionScheme).where(PermissionScheme.is_default.is_(True))).first()
    if scheme:
        return
    scheme = PermissionScheme(
        name="Default Permission Scheme",
        description="Applied to projects without an explicit scheme.",
        is_default=True,
    )
    db.add(scheme)
    db.flush()

    grants: list[PermissionGrant] = []
    # Administrators get every project permission.
    for perm in P.PROJECT_PERMISSIONS:
        grants.append(PermissionGrant(scheme_id=scheme.id, permission=perm, holder_type=P.HOLDER_PROJECT_ROLE, holder_value="Administrators"))
    for perm in _DEV_PERMS:
        grants.append(PermissionGrant(scheme_id=scheme.id, permission=perm, holder_type=P.HOLDER_PROJECT_ROLE, holder_value="Developers"))
    for perm in _VIEWER_PERMS:
        grants.append(PermissionGrant(scheme_id=scheme.id, permission=perm, holder_type=P.HOLDER_PROJECT_ROLE, holder_value="Viewers"))
    db.add_all(grants)
    db.flush()
    log.info("Seeded default permission scheme with %d grants", len(grants))


def seed_admin_group(db: Session) -> None:
    group = db.scalars(select(Group).where(Group.name == SYSTEM_ADMIN_GROUP)).first()
    if not group:
        group = Group(
            name=SYSTEM_ADMIN_GROUP,
            description="Members have full administrative control of the instance.",
            is_system=True,
        )
        db.add(group)
        db.flush()
    # Ensure the global ADMINISTER grant points at this group.
    grant = db.scalars(
        select(GlobalPermissionGrant).where(
            GlobalPermissionGrant.permission == P.ADMINISTER,
            GlobalPermissionGrant.holder_type == P.HOLDER_GROUP,
            GlobalPermissionGrant.holder_value == SYSTEM_ADMIN_GROUP,
        )
    ).first()
    if not grant:
        db.add(GlobalPermissionGrant(permission=P.ADMINISTER, holder_type=P.HOLDER_GROUP, holder_value=SYSTEM_ADMIN_GROUP))
    # Put every site admin into the group.
    for admin in db.scalars(select(User).where(User.is_admin.is_(True))):
        present = db.scalar(
            select(user_groups.c.user_id).where(
                user_groups.c.user_id == admin.id, user_groups.c.group_id == group.id
            )
        )
        if not present:
            db.execute(user_groups.insert().values(user_id=admin.id, group_id=group.id))
    db.commit()


def run_rbac_bootstrap(db: Session) -> None:
    seed_roles(db)
    seed_default_scheme(db)
    db.commit()
    seed_admin_group(db)
