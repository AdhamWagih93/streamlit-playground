"""Trivy client factory."""
from __future__ import annotations

from typing import Optional

from ...config import TrivyMCPServerConfig
from ...utils.client import TrivyClient


_CLIENT: Optional[TrivyClient] = None


def client_from_env() -> TrivyClient:
    """Get or create a TrivyClient from environment configuration."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg = TrivyMCPServerConfig.from_env()
    _CLIENT = TrivyClient(
        cache_dir=cfg.cache_dir,
        timeout_seconds=cfg.timeout_seconds,
        severity=cfg.severity,
        ignore_unfixed=cfg.ignore_unfixed,
        skip_db_update=cfg.skip_db_update,
    )
    return _CLIENT
