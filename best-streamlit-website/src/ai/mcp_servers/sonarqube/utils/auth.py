from __future__ import annotations

import os
from typing import Dict, Optional


def auth_or_error(_client_token: Optional[str]) -> Optional[Dict[str, str]]:
    """Validate client token against environment variable.
    
    Returns error dict if invalid, None if valid.
    """
    expected = os.environ.get("SONARQUBE_MCP_CLIENT_TOKEN", "dev-sonarqube-mcp-token")
    if _client_token != expected:
        return {"error": "Invalid or missing _client_token"}
    return None
