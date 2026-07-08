"""RBAC model — faithful port of the original dashboard's role/team semantics."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ROLES = ["Admin", "CLevel", "Developer", "QC", "Operations"]

# Strict raw-string → role mapping (no loose aliases: prevents accidental elevation).
ROLE_STRICT = {
    "admin": "Admin",
    "clevel": "CLevel",
    "c-level": "CLevel",
    "executive": "CLevel",
    "developer": "Developer",
    "quality-control": "QC",
    "qc": "QC",
    "operator": "Operations",
    "operations": "Operations",
}

# Role → inventory team fields that gate ACTIONS (visibility is broader, see below).
ROLE_TEAM_FIELDS = {
    "Admin": [],
    "CLevel": [],
    "Developer": ["dev_team"],
    "QC": ["qc_team"],
    "Operations": ["uat_team", "prd_team"],
}

ALL_TEAM_FIELDS = ["dev_team", "qc_team", "uat_team", "prd_team", "ops_team", "preprod_team"]

ROLE_ENVS = {
    "Admin": ["dev", "qc", "uat", "prd"],
    "CLevel": ["dev", "qc", "uat", "prd"],
    "Developer": ["dev"],
    "QC": ["qc"],
    "Operations": ["uat", "prd"],
}

ROLE_EVENT_TYPES = {
    "Admin": ["build-develop", "build-release", "deploy", "release", "request", "commit"],
    "CLevel": ["build-develop", "build-release", "deploy", "release", "request", "commit"],
    "Developer": ["commit", "build-develop", "build-release", "deploy"],
    "QC": ["deploy", "release", "request"],
    "Operations": ["deploy", "release", "request"],
}


def team_match_key(s: str) -> str:
    """Case- and separator-insensitive team key: 'My Team'=='my_team'=='My-Team'."""
    return re.sub(r"[\s_\-]+", "", (s or "").strip().lower())


def resolve_roles(raw_roles: list[str]) -> list[str]:
    out: list[str] = []
    for raw in raw_roles:
        role = ROLE_STRICT.get((raw or "").strip().lower())
        if role and role not in out:
            out.append(role)
    return out


def pick_primary(roles: list[str]) -> str:
    if "Admin" in roles:
        return "Admin"
    if "CLevel" in roles:
        return "CLevel"
    return roles[0] if roles else "Developer"


@dataclass
class User:
    username: str
    display_name: str
    email: str
    raw_roles: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)

    @property
    def roles(self) -> list[str]:
        return resolve_roles(self.raw_roles) or ["Developer"]

    @property
    def role(self) -> str:
        return pick_primary(self.roles)

    @property
    def is_admin(self) -> bool:
        return self.role in ("Admin", "CLevel")

    @property
    def team_keys(self) -> set[str]:
        return {team_match_key(t) for t in self.teams if t}

    def union_over_roles(self, table: dict[str, list]) -> list:
        """Order-preserving union of a per-role list across every role the user holds."""
        out: list = []
        for r in self.roles:
            for item in table.get(r, []):
                if item not in out:
                    out.append(item)
        return out

    @property
    def visible_envs(self) -> list[str]:
        return self.union_over_roles(ROLE_ENVS)

    @property
    def visible_event_types(self) -> list[str]:
        return self.union_over_roles(ROLE_EVENT_TYPES)

    def can_see_row(self, row_teams: dict[str, list[str]], view_all: bool = True) -> bool:
        """Visibility: admins see all (unless they scoped down); non-admins match their
        teams against ANY *_team field (deliberately broad — role gates actions, not sight)."""
        if self.is_admin and view_all:
            return True
        mine = self.team_keys
        if not mine:
            return False
        for f in ALL_TEAM_FIELDS:
            for t in row_teams.get(f, []) or []:
                if team_match_key(t) in mine:
                    return True
        return False

    def action_fields(self) -> list[str]:
        return self.union_over_roles(ROLE_TEAM_FIELDS)
