from __future__ import annotations

import inspect
import os
from typing import Any, Dict, Optional

from fastmcp import FastMCP

from .config import KubernetesMCPServerConfig
from .utils.access_mgmt import list_service_accounts as list_service_accounts_impl
from .utils.access_mgmt import list_service_accounts_all as list_service_accounts_all_impl
from .utils.clients import KubernetesClientSet, load_clients
from .utils.cluster import get_cluster_overview as get_cluster_overview_impl
from .utils.cluster import get_cluster_stats as get_cluster_stats_impl
from .utils.cluster import health_check as health_check_impl
from .utils.core_resources import delete_pod as delete_pod_impl
from .utils.core_resources import create_namespace as create_namespace_impl
from .utils.core_resources import get_pod_logs as get_pod_logs_impl
from .utils.core_resources import list_events as list_events_impl
from .utils.core_resources import list_events_all as list_events_all_impl
from .utils.core_resources import list_namespaces as list_namespaces_impl
from .utils.core_resources import list_nodes as list_nodes_impl
from .utils.core_resources import list_pods as list_pods_impl
from .utils.core_resources import list_services as list_services_impl
from .utils.core_resources import list_services_all as list_services_all_impl
from .utils.terminal import kubectl_like as kubectl_like_cmd
from .utils.workloads import list_deployments as list_deployments_impl
from .utils.workloads import list_deployments_all as list_deployments_all_impl
from .utils.workloads import restart_deployment as restart_deployment_impl
from .utils.workloads import scale_deployment as scale_deployment_impl


mcp = FastMCP("kubernetes-mcp")

_CLIENTS: Optional[KubernetesClientSet] = None


def _clients_from_env() -> KubernetesClientSet:
    global _CLIENTS
    if _CLIENTS is not None:
        return _CLIENTS

    cfg = KubernetesMCPServerConfig.from_env()
    _CLIENTS = load_clients(kubeconfig=cfg.kubeconfig, context=cfg.context)
    return _CLIENTS


@mcp.tool
def health_check() -> Dict[str, Any]:
    """Run basic reachability and API responsiveness checks."""
    c = _clients_from_env()
    return health_check_impl(c.core, c.version)


@mcp.tool
def get_cluster_stats() -> Dict[str, Any]:
    """Return high-level counts for common resource types."""
    c = _clients_from_env()
    return get_cluster_stats_impl(c.core, c.apps)


@mcp.tool
def get_cluster_overview() -> Dict[str, Any]:
    """Back-compat overview (nodes, namespaces, pods)."""
    c = _clients_from_env()
    return get_cluster_overview_impl(c.core)


@mcp.tool
def list_namespaces() -> Dict[str, Any]:
    c = _clients_from_env()
    return list_namespaces_impl(c.core)


@mcp.tool
def list_nodes() -> Dict[str, Any]:
    c = _clients_from_env()
    return list_nodes_impl(c.core)


@mcp.tool
def create_namespace(name: str) -> Dict[str, Any]:
    """Create a namespace."""
    c = _clients_from_env()
    return create_namespace_impl(c.core, name=name)


@mcp.tool
def list_deployments(namespace: str = "default") -> Dict[str, Any]:
    c = _clients_from_env()
    return list_deployments_impl(c.apps, namespace=namespace)


@mcp.tool
def list_deployments_all() -> Dict[str, Any]:
    c = _clients_from_env()
    return list_deployments_all_impl(c.apps)


@mcp.tool
def list_pods(namespace: Optional[str] = None) -> Dict[str, Any]:
    c = _clients_from_env()
    return list_pods_impl(c.core, namespace=namespace)


@mcp.tool
def get_pod_logs(name: str, namespace: str = "default", tail_lines: int = 200) -> Dict[str, Any]:
    c = _clients_from_env()
    return get_pod_logs_impl(c.core, name=name, namespace=namespace, tail_lines=tail_lines)


@mcp.tool
def list_services(namespace: str = "default") -> Dict[str, Any]:
    c = _clients_from_env()
    return list_services_impl(c.core, namespace=namespace)


@mcp.tool
def list_services_all() -> Dict[str, Any]:
    c = _clients_from_env()
    return list_services_all_impl(c.core)


@mcp.tool
def list_service_accounts(namespace: str = "default") -> Dict[str, Any]:
    c = _clients_from_env()
    return list_service_accounts_impl(c.core, namespace=namespace)


@mcp.tool
def list_service_accounts_all() -> Dict[str, Any]:
    c = _clients_from_env()
    return list_service_accounts_all_impl(c.core)


@mcp.tool
def list_events(namespace: str = "default", limit: int = 200) -> Dict[str, Any]:
    c = _clients_from_env()
    return list_events_impl(c.core, namespace=namespace, limit=limit)


@mcp.tool
def list_events_all(limit: int = 200) -> Dict[str, Any]:
    c = _clients_from_env()
    return list_events_all_impl(c.core, limit=limit)


@mcp.tool
def scale_deployment(name: str, namespace: str, replicas: int) -> Dict[str, Any]:
    c = _clients_from_env()
    return scale_deployment_impl(c.apps, name=name, namespace=namespace, replicas=replicas)


@mcp.tool
def delete_pod(name: str, namespace: str = "default") -> Dict[str, Any]:
    c = _clients_from_env()
    return delete_pod_impl(c.core, name=name, namespace=namespace)


@mcp.tool
def restart_deployment(name: str, namespace: str = "default") -> Dict[str, Any]:
    c = _clients_from_env()
    return restart_deployment_impl(c.apps, name=name, namespace=namespace)


@mcp.tool
def kubectl_like(command: str) -> Dict[str, Any]:
    """Execute a limited, safe subset of kubectl-style commands."""
    c = _clients_from_env()
    return kubectl_like_cmd(c, command)


def run_stdio() -> None:
    cfg = KubernetesMCPServerConfig.from_env()
    transport = (os.environ.get("MCP_TRANSPORT") or cfg.mcp_transport or "stdio").lower().strip()
    transport = "sse" if transport == "http" else transport

    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    host = os.environ.get("MCP_HOST") or cfg.mcp_host
    port_raw = os.environ.get("MCP_PORT")
    try:
        port = int(port_raw) if port_raw else int(cfg.mcp_port)
    except Exception:
        port = int(cfg.mcp_port)

    sig = inspect.signature(mcp.run)
    kwargs: Dict[str, Any] = {"transport": transport}
    if "host" in sig.parameters:
        kwargs["host"] = host
    if "port" in sig.parameters:
        kwargs["port"] = port

    mcp.run(**kwargs)


if __name__ == "__main__":
    run_stdio()
