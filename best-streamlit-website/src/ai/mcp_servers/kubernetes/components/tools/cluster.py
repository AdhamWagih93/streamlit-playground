"""Kubernetes cluster-level tools."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .client_factory import auth_or_error, clients_from_env
from ...utils.cluster import (
    get_cluster_overview as get_cluster_overview_impl,
    get_cluster_stats as get_cluster_stats_impl,
    health_check as health_check_impl,
)


def health_check(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Run basic reachability and API responsiveness checks."""
    err = auth_or_error(_client_token)
    if err:
        return err
    c = clients_from_env()
    return health_check_impl(c.core, c.version)


def get_cluster_stats(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return high-level counts for common resource types."""
    err = auth_or_error(_client_token)
    if err:
        return err
    c = clients_from_env()
    return get_cluster_stats_impl(c.core, c.apps)


def get_cluster_overview(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Back-compat overview (nodes, namespaces, pods)."""
    err = auth_or_error(_client_token)
    if err:
        return err
    c = clients_from_env()
    return get_cluster_overview_impl(c.core)
