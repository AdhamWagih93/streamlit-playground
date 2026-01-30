"""Kubernetes workload management tools."""
from __future__ import annotations

from typing import Any, Dict

from .client_factory import clients_from_env
from ...utils.workloads import (
    list_deployments as list_deployments_impl,
    list_deployments_all as list_deployments_all_impl,
    restart_deployment as restart_deployment_impl,
    scale_deployment as scale_deployment_impl,
)


def list_deployments(namespace: str = "default") -> Dict[str, Any]:
    """List deployments in a namespace."""
    c = clients_from_env()
    return list_deployments_impl(c.apps, namespace=namespace)


def list_deployments_all() -> Dict[str, Any]:
    """List deployments across all namespaces."""
    c = clients_from_env()
    return list_deployments_all_impl(c.apps)


def scale_deployment(name: str, namespace: str, replicas: int) -> Dict[str, Any]:
    """Scale a deployment to a specific number of replicas."""
    c = clients_from_env()
    return scale_deployment_impl(c.apps, name=name, namespace=namespace, replicas=replicas)


def restart_deployment(name: str, namespace: str = "default") -> Dict[str, Any]:
    """Restart a deployment by triggering a rollout."""
    c = clients_from_env()
    return restart_deployment_impl(c.apps, name=name, namespace=namespace)
