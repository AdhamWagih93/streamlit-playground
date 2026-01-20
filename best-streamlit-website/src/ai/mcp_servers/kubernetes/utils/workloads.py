from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from kubernetes.client import ApiException


def list_deployments(apps_api: Any, namespace: str = "default") -> Dict[str, Any]:
    try:
        items = apps_api.list_namespaced_deployment(namespace=namespace).items
    except ApiException as exc:
        return {"ok": False, "error": str(exc)}

    out: List[Dict[str, Any]] = []
    for d in items:
        spec = d.spec
        status = d.status
        out.append(
            {
                "name": d.metadata.name,
                "namespace": d.metadata.namespace,
                "replicas": getattr(spec, "replicas", None),
                "readyReplicas": getattr(status, "ready_replicas", None),
                "availableReplicas": getattr(status, "available_replicas", None),
            }
        )
    return {"ok": True, "deployments": out}


def list_deployments_all(apps_api: Any) -> Dict[str, Any]:
    try:
        items = apps_api.list_deployment_for_all_namespaces().items
    except ApiException as exc:
        return {"ok": False, "error": str(exc)}

    out: List[Dict[str, Any]] = []
    for d in items:
        spec = d.spec
        status = d.status
        out.append(
            {
                "name": d.metadata.name,
                "namespace": d.metadata.namespace,
                "replicas": getattr(spec, "replicas", None),
                "readyReplicas": getattr(status, "ready_replicas", None),
                "availableReplicas": getattr(status, "available_replicas", None),
            }
        )
    return {"ok": True, "deployments": out}


def scale_deployment(apps_api: Any, name: str, namespace: str, replicas: int) -> Dict[str, Any]:
    body = {"spec": {"replicas": replicas}}
    try:
        resp = apps_api.patch_namespaced_deployment_scale(name=name, namespace=namespace, body=body)
    except ApiException as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "name": resp.metadata.name, "namespace": resp.metadata.namespace, "replicas": resp.spec.replicas}


def restart_deployment(apps_api: Any, name: str, namespace: str = "default") -> Dict[str, Any]:
    restarted_at = datetime.now(timezone.utc).isoformat()
    patch = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": restarted_at}}}}}

    try:
        resp = apps_api.patch_namespaced_deployment(name=name, namespace=namespace, body=patch)
    except ApiException as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "name": resp.metadata.name, "namespace": resp.metadata.namespace, "restartedAt": restarted_at}
