"""Aggregate every domain router under the API prefix.

Each module in app/api/routes defines a module-level ``router = APIRouter()``.
This file is the single place they are mounted, so route modules stay decoupled.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    admin,
    agile,
    analytics,
    auth,
    groups,
    issues,
    meta,
    notifications,
    notify_prefs,
    permschemes,
    projects,
    roles,
    search,
    sync,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(meta.router, prefix="/meta", tags=["meta"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(issues.router, prefix="/issues", tags=["issues"])
api_router.include_router(agile.router, prefix="/agile", tags=["agile"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(
    notify_prefs.router, prefix="/notification-preferences", tags=["notification-preferences"]
)
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(groups.router, prefix="/groups", tags=["groups"])
api_router.include_router(roles.router, prefix="/roles", tags=["roles"])
api_router.include_router(permschemes.router, prefix="/permission-schemes", tags=["permission-schemes"])
api_router.include_router(sync.router, prefix="/sync", tags=["sync"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
