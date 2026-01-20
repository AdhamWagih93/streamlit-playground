from __future__ import annotations

import os
from typing import Any, Dict, Optional

from ..config import JenkinsMCPServerConfig


def auth_or_error(client_token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Enforce a second auth layer between MCP clients and the Jenkins MCP server.

    Server-side configuration:
    - JENKINS_MCP_CLIENT_TOKEN

    Client calls must pass the same value as the tool arg `_client_token`.
    """

    # Env-first; fallback to a local-dev token so the server can run without
    # environment configuration when developing locally.
    expected = os.environ.get("JENKINS_MCP_CLIENT_TOKEN") or JenkinsMCPServerConfig.DEFAULT_DEV_CLIENT_TOKEN

    if not client_token or client_token != expected:
        return {
            "ok": False,
            "error": "Unauthorized Jenkins MCP client.",
            "hint": "Missing/invalid _client_token.",
        }

    return None
