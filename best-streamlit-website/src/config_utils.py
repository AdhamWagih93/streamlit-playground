from __future__ import annotations

import os
from typing import Optional


def env_str(name: str, default: str, *, strip: bool = True) -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip() if strip else value


def env_optional_str(name: str, default: Optional[str] = None, *, strip: bool = True) -> Optional[str]:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip() if strip else value
    return value or default


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default
