"""Aggregate every domain router under the API prefix.

Each module in app/api/routes defines a module-level ``router = APIRouter()``.
This file is the single place they are mounted, so route modules stay decoupled.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    agile,
    auth,
    issues,
    meta,
    notifications,
    projects,
    search,
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
