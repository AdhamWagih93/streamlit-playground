"""Shared RBAC scoping over the demo world — every demo slice filters through here."""
from __future__ import annotations

from ...auth.rbac import User
from .world import App, get_world


def visible_apps(user: User, view_all: bool = True) -> list[App]:
    return [a for a in get_world().apps if user.can_see_row(a.teams, view_all)]


def visible_app_names(user: User, view_all: bool = True) -> set[str]:
    return {a.application for a in visible_apps(user, view_all)}


def app_by_name(project: str, application: str) -> App | None:
    for a in get_world().apps:
        if a.project == project and a.application == application:
            return a
    return None
