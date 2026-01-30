"""Git client factory."""
from __future__ import annotations

from typing import Optional

from ...config import GitMCPServerConfig
from ...utils.client import GitClient


_CLIENT: Optional[GitClient] = None


def client_from_env() -> GitClient:
    """Get or create a GitClient from environment configuration."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg = GitMCPServerConfig.from_env()
    _CLIENT = GitClient(
        repo_path=cfg.repo_path,
        timeout_seconds=cfg.timeout_seconds,
    )
    return _CLIENT
