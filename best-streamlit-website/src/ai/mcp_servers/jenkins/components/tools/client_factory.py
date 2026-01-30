"""Jenkins client factory and auth utilities."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ...config import JenkinsMCPServerConfig
from ...utils.auth import auth_or_error
from ...utils.client import JenkinsAuthConfig, JenkinsMCPServer


_CLIENT: Optional[JenkinsMCPServer] = None


def jenkins_client_from_env() -> JenkinsMCPServer:
    """Get or create a Jenkins client from environment configuration."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg_from_env = JenkinsMCPServerConfig.from_env()
    cfg = JenkinsAuthConfig(
        base_url=cfg_from_env.base_url,
        username=cfg_from_env.username,
        api_token=cfg_from_env.api_token,
        verify_ssl=cfg_from_env.verify_ssl,
    )
    _CLIENT = JenkinsMCPServer(cfg)
    return _CLIENT


def check_auth(client_token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Check client authentication."""
    return auth_or_error(client_token)
