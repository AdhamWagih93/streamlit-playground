"""Kubernetes core resources tools."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .client_factory import auth_or_error, clients_from_env
from ...utils.access_mgmt import (
    list_service_accounts as list_service_accounts_impl,
    list_service_accounts_all as list_service_accounts_all_impl,
)
from ...utils.core_resources import (
    create_namespace as create_namespace_impl,
    delete_pod as delete_pod_impl,
    get_pod_logs as get_pod_logs_impl,
    list_events as list_events_impl,
    list_events_all as list_events_all_impl,
    list_namespaces as list_namespaces_impl,
    list_nodes as list_nodes_impl,
    list_pods as list_pods_impl,
    list_services as list_services_impl,
    list_services_all as list_services_all_impl,
)


def list_namespaces(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List all namespaces."""
    err = auth_or_error(_client_token)
    if err:
        return err
    c = clients_from_env()
    return list_namespaces_impl(c.core)


def list_nodes(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List all nodes in the cluster."""
    err = auth_or_error(_client_token)
    if err:
        return err
    c = clients_from_env()
    return list_nodes_impl(c.core)


def create_namespace(name: str) -> Dict[str, Any]:
    """Create a namespace."""
    c = clients_from_env()
    return create_namespace_impl(c.core, name=name)


def list_pods(namespace: Optional[str] = None) -> Dict[str, Any]:
    """List pods in a namespace or all namespaces."""
    c = clients_from_env()
    return list_pods_impl(c.core, namespace=namespace)


def get_pod_logs(name: str, namespace: str = "default", tail_lines: int = 200) -> Dict[str, Any]:
    """Get logs from a pod."""
    c = clients_from_env()
    return get_pod_logs_impl(c.core, name=name, namespace=namespace, tail_lines=tail_lines)


def delete_pod(name: str, namespace: str = "default") -> Dict[str, Any]:
    """Delete a pod."""
    c = clients_from_env()
    return delete_pod_impl(c.core, name=name, namespace=namespace)


def list_services(namespace: str = "default") -> Dict[str, Any]:
    """List services in a namespace."""
    c = clients_from_env()
    return list_services_impl(c.core, namespace=namespace)


def list_services_all() -> Dict[str, Any]:
    """List services across all namespaces."""
    c = clients_from_env()
    return list_services_all_impl(c.core)


def list_service_accounts(namespace: str = "default") -> Dict[str, Any]:
    """List service accounts in a namespace."""
    c = clients_from_env()
    return list_service_accounts_impl(c.core, namespace=namespace)


def list_service_accounts_all() -> Dict[str, Any]:
    """List service accounts across all namespaces."""
    c = clients_from_env()
    return list_service_accounts_all_impl(c.core)


def list_events(namespace: str = "default", limit: int = 200) -> Dict[str, Any]:
    """List events in a namespace."""
    c = clients_from_env()
    return list_events_impl(c.core, namespace=namespace, limit=limit)


def list_events_all(limit: int = 200) -> Dict[str, Any]:
    """List events across all namespaces."""
    c = clients_from_env()
    return list_events_all_impl(c.core, limit=limit)
