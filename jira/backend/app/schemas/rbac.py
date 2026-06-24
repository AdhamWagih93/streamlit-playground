"""Schemas for groups, project roles, and permission schemes."""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ORMModel
from app.schemas.user import UserBrief


class GroupBrief(ORMModel):
    id: int
    name: str
    description: str | None = None
    directory_source: str | None = None
    is_system: bool = False


class GroupOut(GroupBrief):
    members: list[UserBrief] = []


class GroupIn(BaseModel):
    name: str
    description: str | None = None


class GroupMemberIn(BaseModel):
    user_id: int


class ProjectRoleOut(ORMModel):
    id: int
    name: str
    description: str | None = None
    is_default: bool = False


class ProjectRoleIn(BaseModel):
    name: str
    description: str | None = None


class RoleActorOut(ORMModel):
    id: int
    role_id: int
    user: UserBrief | None = None
    group: GroupBrief | None = None


class RoleActorIn(BaseModel):
    role_id: int
    user_id: int | None = None
    group_id: int | None = None


class GrantOut(ORMModel):
    id: int
    permission: str
    holder_type: str
    holder_value: str | None = None


class GrantIn(BaseModel):
    permission: str
    holder_type: str  # group | user | role | special
    holder_value: str | None = None


class PermissionSchemeBrief(ORMModel):
    id: int
    name: str
    description: str | None = None
    is_default: bool = False


class PermissionSchemeOut(PermissionSchemeBrief):
    grants: list[GrantOut] = []


class PermissionSchemeIn(BaseModel):
    name: str
    description: str | None = None


class GlobalGrantOut(ORMModel):
    id: int
    permission: str
    holder_type: str
    holder_value: str


class GlobalGrantIn(BaseModel):
    permission: str
    holder_type: str  # group | user
    holder_value: str


class PermissionDef(BaseModel):
    key: str
    description: str


class PermissionCatalog(BaseModel):
    global_permissions: list[PermissionDef]
    project_permissions: list[PermissionDef]
    holder_types: list[str]
    special_holders: list[str]
