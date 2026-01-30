"""Jenkins server info tools."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .client_factory import check_auth, jenkins_client_from_env


def get_server_info(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return Jenkins root API information."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_server_info()


def get_system_info(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return basic system info (version, node mode, quieting, etc.)."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_system_info()
