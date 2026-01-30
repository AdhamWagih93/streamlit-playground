"""Jenkins admin tools (nodes, views, plugins)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .client_factory import check_auth, jenkins_client_from_env


def list_nodes(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List nodes (agents) connected to Jenkins."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().list_nodes()


def get_node_info(node_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return information about a specific node."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_node_info(node_name)


def list_views(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List all Jenkins views and their URLs."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().list_views()


def get_view_info(view_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return information and job list for a specific view."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_view_info(view_name)


def list_plugins(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List installed plugins and their versions."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().list_plugins()
