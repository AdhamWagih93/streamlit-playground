"""Provider dispatch: routers call `impl("<slice>")` and get the demo or live module.

Each slice lives twice with the same function signatures:
    app/providers/demo/<slice>.py   — seeded in-memory world (local dev)
    app/providers/live/<slice>.py   — real integrations (deployment)

Live modules that aren't implemented yet raise a clear 503 instead of silently
falling back to demo data.
"""
from __future__ import annotations

import importlib

from fastapi import HTTPException

from ..config import get_settings


def impl(slice_name: str):
    mode = get_settings().data_mode
    try:
        return importlib.import_module(f"app.providers.{mode}.{slice_name}")
    except ModuleNotFoundError as exc:
        if mode == "live":
            raise HTTPException(
                status_code=503,
                detail=f"Live provider for '{slice_name}' is not implemented/configured yet ({exc}).",
            )
        raise
