"""Project, membership, component and version schemas."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.schemas.common import ORMModel
from app.schemas.user import UserBrief


class ComponentOut(ORMModel):
    id: int
    name: str
    description: str | None = None
    lead: UserBrief | None = None


class ComponentIn(BaseModel):
    name: str
    description: str | None = None
    lead_id: int | None = None


class VersionOut(ORMModel):
    id: int
    name: str
    description: str | None = None
    released: bool
    archived: bool
    release_date: date | None = None


class VersionIn(BaseModel):
    name: str
    description: str | None = None
    released: bool = False
    archived: bool = False
    release_date: date | None = None


class MemberOut(ORMModel):
    id: int
    role: str
    user: UserBrief


class MemberIn(BaseModel):
    user_id: int
    role: str = "member"


class ProjectBrief(ORMModel):
    id: int
    key: str
    name: str
    project_type: str
    avatar_color: str


class PermissionSchemeRef(ORMModel):
    id: int
    name: str


class ProjectOut(ProjectBrief):
    description: str | None = None
    lead: UserBrief | None = None
    is_archived: bool = False
    permission_scheme_id: int | None = None
    permission_scheme: PermissionSchemeRef | None = None
    components: list[ComponentOut] = []
    versions: list[VersionOut] = []


class ProjectCreate(BaseModel):
    key: str
    name: str
    description: str | None = None
    project_type: str = "software"
    avatar_color: str = "#2563eb"
    lead_id: int | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    avatar_color: str | None = None
    lead_id: int | None = None
    is_archived: bool | None = None
