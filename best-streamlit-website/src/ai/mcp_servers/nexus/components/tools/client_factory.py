"""Nexus client factory and auth utilities."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from ...config import NexusMCPServerConfig
from ...utils.client import NexusClient


_CLIENT: Optional[NexusClient] = None


def client_from_env() -> NexusClient:
    """Get or create a Nexus client from environment configuration."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg = NexusMCPServerConfig.from_env()
    _CLIENT = NexusClient(
        base_url=cfg.base_url,
        username=cfg.username,
        password=cfg.password,
        token=cfg.token,
        verify_ssl=cfg.verify_ssl,
    )
    return _CLIENT


def auth_or_error(_client_token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate client token for MCP tools.

    If NEXUS_MCP_CLIENT_TOKEN is not configured, tools are open (useful for
    local dev). When set, callers must pass matching `_client_token`.
    """
    expected = os.environ.get("NEXUS_MCP_CLIENT_TOKEN")
    if not expected:
        return None
    if _client_token != expected:
        return {
            "ok": False,
            "error": "unauthorized",
            "hint": "Invalid or missing client token.",
        }
    return None


def require_raw_enabled() -> Optional[Dict[str, Any]]:
    """Check if raw request is enabled."""
    cfg = NexusMCPServerConfig.from_env()
    if not cfg.allow_raw:
        return {
            "ok": False,
            "error": "nexus_raw_request is disabled. Set NEXUS_ALLOW_RAW=true to enable.",
        }
    return None
