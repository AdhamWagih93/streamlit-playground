from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import os
import shlex

from fastmcp import FastMCP
from kubernetes import client, config
from kubernetes.client import ApiException


@dataclass
class KubernetesConfig:
    kubeconfig: Optional[str] = None
    context: Optional[str] = None


class KubernetesMCPServer:
    """Thin wrapper around the Kubernetes Python client for MCP tools.

    This server is intended to run locally (same machine as Streamlit) and
    connects to the current kubeconfig/context by default, or to the
    values provided via environment variables:

    - K8S_KUBECONFIG (path to kubeconfig, falls back to default)
    - K8S_CONTEXT (optional kubectl context name)
    """

    def __init__(self, cfg: KubernetesConfig) -> None:
        self.cfg = cfg
        self._load_config()

    def _load_config(self) -> None:
        if self.cfg.kubeconfig:
            if self.cfg.context:
                config.load_kube_config(config_file=self.cfg.kubeconfig, context=self.cfg.context)
            else:
                config.load_kube_config(config_file=self.cfg.kubeconfig)
        else:
            # Fallback to default kubeconfig search (~/.kube/config, etc.)
            if self.cfg.context:
                config.load_kube_config(context=self.cfg.context)
            else:
                config.load_kube_config()

        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.networking = client.NetworkingV1Api()

    # ---- Cluster & namespace overview ----

    def get_cluster_overview(self) -> Dict[str, Any]:
        """Return high-level info: node count, namespace count, pod count.

        This method is intentionally resilient to partial RBAC permissions.
        It will return partial counts with per-section errors when some list
        calls are forbidden.
        """

        nodes_count: int | None = None
        namespaces_count: int | None = None
        pods_count: int | None = None
        errors: List[Dict[str, Any]] = []

        try:
            nodes_count = len(self.core.list_node().items)
        except ApiException as exc:
            errors.append({"area": "nodes", "error": str(exc)})

        try:
            namespaces_count = len(self.core.list_namespace().items)
        except ApiException as exc:
            errors.append({"area": "namespaces", "error": str(exc)})

        try:
            pods_count = len(self.core.list_pod_for_all_namespaces().items)
        except ApiException as exc:
            errors.append({"area": "pods", "error": str(exc)})

        ok = any(v is not None for v in (nodes_count, namespaces_count, pods_count))
        result: Dict[str, Any] = {
            "ok": ok,
            "nodes": nodes_count,
            "namespaces": namespaces_count,
            "pods": pods_count,
        }
        if errors:
            result["errors"] = errors
        return result

    def list_namespaces(self) -> Dict[str, Any]:
        """List namespaces with basic status."""

        try:
            items = self.core.list_namespace().items
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        out: List[Dict[str, Any]] = []
        for ns in items:
            out.append(
                {
                    "name": ns.metadata.name,
                    "status": getattr(ns.status, "phase", None),
                    "labels": ns.metadata.labels or {},
                    "creationTimestamp": ns.metadata.creation_timestamp.isoformat() if ns.metadata.creation_timestamp else None,
                }
            )
        return {"ok": True, "namespaces": out}

    def list_nodes(self) -> Dict[str, Any]:
        """List nodes with capacity and conditions."""

        try:
            items = self.core.list_node().items
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        out: List[Dict[str, Any]] = []
        for node in items:
            capacity = node.status.capacity or {}
            conditions = [
                {
                    "type": c.type,
                    "status": c.status,
                    "reason": c.reason,
                    "message": c.message,
                }
                for c in (node.status.conditions or [])
            ]
            out.append(
                {
                    "name": node.metadata.name,
                    "labels": node.metadata.labels or {},
                    "capacity": capacity,
                    "conditions": conditions,
                }
            )
        return {"ok": True, "nodes": out}

    # ---- Workloads & pods ----

    def list_deployments(self, namespace: str = "default") -> Dict[str, Any]:
        """List deployments in a namespace with replica counts."""

        try:
            items = self.apps.list_namespaced_deployment(namespace=namespace).items
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

    def list_deployments_all(self) -> Dict[str, Any]:
        """List deployments across all namespaces with replica counts."""

        try:
            items = self.apps.list_deployment_for_all_namespaces().items
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

    def list_services(self, namespace: str = "default") -> Dict[str, Any]:
        """List services in a namespace."""

        try:
            items = self.core.list_namespaced_service(namespace=namespace).items
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        out: List[Dict[str, Any]] = []
        for s in items:
            spec = s.spec
            out.append(
                {
                    "name": s.metadata.name,
                    "namespace": s.metadata.namespace,
                    "type": getattr(spec, "type", None),
                    "clusterIP": getattr(spec, "cluster_ip", None),
                    "ports": [
                        {
                            "port": getattr(p, "port", None),
                            "targetPort": getattr(p, "target_port", None),
                            "protocol": getattr(p, "protocol", None),
                            "name": getattr(p, "name", None),
                        }
                        for p in (getattr(spec, "ports", None) or [])
                    ],
                }
            )
        return {"ok": True, "services": out}

    def list_services_all(self) -> Dict[str, Any]:
        """List services across all namespaces."""

        try:
            items = self.core.list_service_for_all_namespaces().items
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        out: List[Dict[str, Any]] = []
        for s in items:
            spec = s.spec
            out.append(
                {
                    "name": s.metadata.name,
                    "namespace": s.metadata.namespace,
                    "type": getattr(spec, "type", None),
                    "clusterIP": getattr(spec, "cluster_ip", None),
                    "ports": [
                        {
                            "port": getattr(p, "port", None),
                            "targetPort": getattr(p, "target_port", None),
                            "protocol": getattr(p, "protocol", None),
                            "name": getattr(p, "name", None),
                        }
                        for p in (getattr(spec, "ports", None) or [])
                    ],
                }
            )
        return {"ok": True, "services": out}

    def list_events(self, namespace: str = "default", limit: int = 200) -> Dict[str, Any]:
        """List recent events in a namespace."""

        try:
            items = self.core.list_namespaced_event(namespace=namespace).items
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        out: List[Dict[str, Any]] = []
        for e in items:
            involved = e.involved_object
            out.append(
                {
                    "namespace": e.metadata.namespace,
                    "type": getattr(e, "type", None),
                    "reason": getattr(e, "reason", None),
                    "message": getattr(e, "message", None),
                    "count": getattr(e, "count", None),
                    "firstTimestamp": e.first_timestamp.isoformat() if e.first_timestamp else None,
                    "lastTimestamp": e.last_timestamp.isoformat() if e.last_timestamp else None,
                    "involvedObject": {
                        "kind": getattr(involved, "kind", None),
                        "name": getattr(involved, "name", None),
                        "namespace": getattr(involved, "namespace", None),
                    },
                }
            )

        # Sort newest-first and apply limit
        def _event_key(ev: Dict[str, Any]) -> str:
            return ev.get("lastTimestamp") or ev.get("firstTimestamp") or ""

        out.sort(key=_event_key, reverse=True)
        return {"ok": True, "events": out[: max(0, int(limit))]}

    def list_events_all(self, limit: int = 200) -> Dict[str, Any]:
        """List recent events across all namespaces."""

        try:
            items = self.core.list_event_for_all_namespaces().items
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        out: List[Dict[str, Any]] = []
        for e in items:
            involved = e.involved_object
            out.append(
                {
                    "namespace": e.metadata.namespace,
                    "type": getattr(e, "type", None),
                    "reason": getattr(e, "reason", None),
                    "message": getattr(e, "message", None),
                    "count": getattr(e, "count", None),
                    "firstTimestamp": e.first_timestamp.isoformat() if e.first_timestamp else None,
                    "lastTimestamp": e.last_timestamp.isoformat() if e.last_timestamp else None,
                    "involvedObject": {
                        "kind": getattr(involved, "kind", None),
                        "name": getattr(involved, "name", None),
                        "namespace": getattr(involved, "namespace", None),
                    },
                }
            )

        def _event_key(ev: Dict[str, Any]) -> str:
            return ev.get("lastTimestamp") or ev.get("firstTimestamp") or ""

        out.sort(key=_event_key, reverse=True)
        return {"ok": True, "events": out[: max(0, int(limit))]}

    def list_pods(self, namespace: Optional[str] = None) -> Dict[str, Any]:
        """List pods (optionally restricted to a namespace)."""

        try:
            if namespace:
                items = self.core.list_namespaced_pod(namespace=namespace).items
            else:
                items = self.core.list_pod_for_all_namespaces().items
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        out: List[Dict[str, Any]] = []
        for p in items:
            status = p.status
            out.append(
                {
                    "name": p.metadata.name,
                    "namespace": p.metadata.namespace,
                    "phase": getattr(status, "phase", None),
                    "hostIP": getattr(status, "host_ip", None),
                    "podIP": getattr(status, "pod_ip", None),
                    "startTime": status.start_time.isoformat() if status.start_time else None,
                    "containers": [c.name for c in (p.spec.containers or [])],
                }
            )
        return {"ok": True, "pods": out}

    def get_pod_logs(self, name: str, namespace: str = "default", tail_lines: int = 200) -> Dict[str, Any]:
        """Return the last N lines of logs for a pod."""

        try:
            log_text = self.core.read_namespaced_pod_log(
                name=name,
                namespace=namespace,
                tail_lines=tail_lines,
            )
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "logs": log_text}

    # ---- Controlled actions ----

    def scale_deployment(self, name: str, namespace: str, replicas: int) -> Dict[str, Any]:
        """Scale a deployment to a specific replica count."""

        body = {"spec": {"replicas": replicas}}
        try:
            resp = self.apps.patch_namespaced_deployment_scale(
                name=name,
                namespace=namespace,
                body=body,
            )
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "name": resp.metadata.name,
            "namespace": resp.metadata.namespace,
            "replicas": resp.spec.replicas,
        }

    def delete_pod(self, name: str, namespace: str = "default") -> Dict[str, Any]:
        """Delete a pod (useful for restarting when controlled)."""

        try:
            self.core.delete_namespaced_pod(name=name, namespace=namespace)
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "name": name, "namespace": namespace}

    def restart_deployment(self, name: str, namespace: str = "default") -> Dict[str, Any]:
        """Trigger a rollout restart for a deployment by patching a pod-template annotation."""

        restarted_at = datetime.now(timezone.utc).isoformat()
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": restarted_at,
                        }
                    }
                }
            }
        }

        try:
            resp = self.apps.patch_namespaced_deployment(name=name, namespace=namespace, body=patch)
        except ApiException as exc:
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "name": resp.metadata.name,
            "namespace": resp.metadata.namespace,
            "restartedAt": restarted_at,
        }


# ---- FastMCP server definition ----

mcp = FastMCP("kubernetes-mcp")


def _k8s_client_from_env() -> KubernetesMCPServer:
    kubeconfig = os.environ.get("K8S_KUBECONFIG") or None
    context = os.environ.get("K8S_CONTEXT") or None
    cfg = KubernetesConfig(kubeconfig=kubeconfig, context=context)
    return KubernetesMCPServer(cfg)


@mcp.tool
def get_cluster_overview() -> Dict[str, Any]:
    """Return high-level Kubernetes cluster info (nodes, namespaces, pods)."""

    return _k8s_client_from_env().get_cluster_overview()


@mcp.tool
def list_namespaces() -> Dict[str, Any]:
    """List namespaces with basic status."""

    return _k8s_client_from_env().list_namespaces()


@mcp.tool
def list_nodes() -> Dict[str, Any]:
    """List nodes with capacity and conditions."""

    return _k8s_client_from_env().list_nodes()


@mcp.tool
def list_deployments(namespace: str = "default") -> Dict[str, Any]:
    """List deployments in a namespace with replica counts."""

    return _k8s_client_from_env().list_deployments(namespace=namespace)


@mcp.tool
def list_deployments_all() -> Dict[str, Any]:
    """List deployments across all namespaces with replica counts."""

    return _k8s_client_from_env().list_deployments_all()


@mcp.tool
def list_pods(namespace: Optional[str] = None) -> Dict[str, Any]:
    """List pods (all namespaces if none specified)."""

    return _k8s_client_from_env().list_pods(namespace=namespace)


@mcp.tool
def get_pod_logs(name: str, namespace: str = "default", tail_lines: int = 200) -> Dict[str, Any]:
    """Return the last N lines of logs for a pod."""

    return _k8s_client_from_env().get_pod_logs(name=name, namespace=namespace, tail_lines=tail_lines)


@mcp.tool
def list_services(namespace: str = "default") -> Dict[str, Any]:
    """List services in a namespace."""

    return _k8s_client_from_env().list_services(namespace=namespace)


@mcp.tool
def list_services_all() -> Dict[str, Any]:
    """List services across all namespaces."""

    return _k8s_client_from_env().list_services_all()


@mcp.tool
def list_events(namespace: str = "default", limit: int = 200) -> Dict[str, Any]:
    """List recent events in a namespace."""

    return _k8s_client_from_env().list_events(namespace=namespace, limit=limit)


@mcp.tool
def list_events_all(limit: int = 200) -> Dict[str, Any]:
    """List recent events across all namespaces."""

    return _k8s_client_from_env().list_events_all(limit=limit)


@mcp.tool
def scale_deployment(name: str, namespace: str, replicas: int) -> Dict[str, Any]:
    """Scale a deployment to a specific replica count."""

    return _k8s_client_from_env().scale_deployment(name=name, namespace=namespace, replicas=replicas)


@mcp.tool
def delete_pod(name: str, namespace: str = "default") -> Dict[str, Any]:
    """Delete a pod (e.g. to trigger controlled restarts)."""

    return _k8s_client_from_env().delete_pod(name=name, namespace=namespace)


@mcp.tool
def restart_deployment(name: str, namespace: str = "default") -> Dict[str, Any]:
    """Trigger a rollout restart for a deployment."""

    return _k8s_client_from_env().restart_deployment(name=name, namespace=namespace)


@mcp.tool
def kubectl_like(command: str) -> Dict[str, Any]:
    """Execute a limited, safe subset of kubectl-style commands.

    Supported patterns (no global state changes beyond the existing helpers):

    - ``kubectl get pods [-n NAMESPACE]`` / ``get pods``
    - ``kubectl get nodes`` / ``get nodes``
    - ``kubectl get namespaces`` / ``get ns``
    - ``kubectl get deployments [-n NAMESPACE]`` / ``get deploy``
    - ``kubectl logs POD -n NAMESPACE [--tail=N]``
    - ``kubectl delete pod POD -n NAMESPACE``
    - ``kubectl scale deployment DEPLOY -n NAMESPACE --replicas=N``

    Any unsupported verb or resource returns a structured error with hints
    rather than executing arbitrary commands.
    """

    server = _k8s_client_from_env()
    raw = command.strip()
    if not raw:
        return {
            "ok": False,
            "error": "Empty command.",
            "hint": "Examples: 'get pods -n default', 'logs my-pod -n default', 'scale deployment my-app -n default --replicas=3'",
        }

    try:
        tokens = shlex.split(raw)
    except ValueError as exc:  # malformed quotes, etc.
        return {"ok": False, "error": f"Could not parse command: {exc}", "raw": raw}

    if not tokens:
        return {"ok": False, "error": "No tokens parsed.", "raw": raw}

    idx = 0
    if tokens[0] == "kubectl":
        idx += 1
    if idx >= len(tokens):
        return {"ok": False, "error": "Missing verb after 'kubectl'.", "raw": raw}

    verb = tokens[idx]
    idx += 1

    def _parse_namespace(start: int) -> tuple[Optional[str], int]:
        ns: Optional[str] = None
        i = start
        while i < len(tokens):
            t = tokens[i]
            if t in ("-n", "--namespace") and i + 1 < len(tokens):
                ns = tokens[i + 1]
                i += 2
            else:
                i += 1
        return ns, i

    if verb == "get":
        if idx >= len(tokens):
            return {"ok": False, "error": "Missing resource for 'get'.", "raw": raw}
        resource = tokens[idx]
        idx += 1
        ns, _ = _parse_namespace(idx)

        if resource in ("pods", "po"):
            res = server.list_pods(namespace=ns)
            return {"ok": True, "verb": "get", "resource": "pods", "namespace": ns, "raw": raw, "result": res}
        if resource in ("nodes", "no"):
            res = server.list_nodes()
            return {"ok": True, "verb": "get", "resource": "nodes", "raw": raw, "result": res}
        if resource in ("namespaces", "ns"):
            res = server.list_namespaces()
            return {"ok": True, "verb": "get", "resource": "namespaces", "raw": raw, "result": res}
        if resource in ("deployments", "deploy", "deployment"):
            ns = ns or "default"
            res = server.list_deployments(namespace=ns)
            return {"ok": True, "verb": "get", "resource": "deployments", "namespace": ns, "raw": raw, "result": res}

        if resource in ("services", "svc", "service"):
            ns = ns or "default"
            res = server.list_services(namespace=ns)
            return {"ok": True, "verb": "get", "resource": "services", "namespace": ns, "raw": raw, "result": res}

        if resource in ("events", "ev"):
            ns = ns or "default"
            res = server.list_events(namespace=ns)
            return {"ok": True, "verb": "get", "resource": "events", "namespace": ns, "raw": raw, "result": res}

        return {
            "ok": False,
            "error": f"Unsupported resource for 'get': {resource}",
            "raw": raw,
            "hint": "Supported: pods, nodes, namespaces, deployments, services, events.",
        }

    if verb == "logs":
        if idx >= len(tokens):
            return {"ok": False, "error": "Missing pod name for 'logs'.", "raw": raw}
        pod_name = tokens[idx]
        idx += 1
        namespace = "default"
        tail_lines = 200
        i = idx
        while i < len(tokens):
            t = tokens[i]
            if t in ("-n", "--namespace") and i + 1 < len(tokens):
                namespace = tokens[i + 1]
                i += 2
            elif t.startswith("--tail="):
                try:
                    tail_lines = int(t.split("=", 1)[1])
                except ValueError:
                    pass
                i += 1
            else:
                i += 1
        res = server.get_pod_logs(name=pod_name, namespace=namespace, tail_lines=tail_lines)
        return {
            "ok": True,
            "verb": "logs",
            "pod": pod_name,
            "namespace": namespace,
            "tail_lines": tail_lines,
            "raw": raw,
            "result": res,
        }

    if verb == "delete":
        if idx >= len(tokens):
            return {"ok": False, "error": "Missing resource for 'delete'.", "raw": raw}
        resource = tokens[idx]
        idx += 1
        if resource not in ("pod", "po"):
            return {"ok": False, "error": f"Unsupported resource for 'delete': {resource}", "raw": raw}
        if idx >= len(tokens):
            return {"ok": False, "error": "Missing pod name for 'delete pod'.", "raw": raw}
        pod_name = tokens[idx]
        namespace, _ = _parse_namespace(idx + 1)
        namespace = namespace or "default"
        res = server.delete_pod(name=pod_name, namespace=namespace)
        return {
            "ok": True,
            "verb": "delete",
            "resource": "pod",
            "pod": pod_name,
            "namespace": namespace,
            "raw": raw,
            "result": res,
        }

    if verb == "scale":
        if idx >= len(tokens):
            return {"ok": False, "error": "Missing resource for 'scale'.", "raw": raw}
        resource = tokens[idx]
        idx += 1
        if resource not in ("deployment", "deploy"):
            return {"ok": False, "error": f"Unsupported resource for 'scale': {resource}", "raw": raw}
        if idx >= len(tokens):
            return {"ok": False, "error": "Missing deployment name for 'scale deployment'.", "raw": raw}
        deploy_name = tokens[idx]
        idx += 1
        namespace = "default"
        replicas: Optional[int] = None
        i = idx
        while i < len(tokens):
            t = tokens[i]
            if t in ("-n", "--namespace") and i + 1 < len(tokens):
                namespace = tokens[i + 1]
                i += 2
            elif t.startswith("--replicas="):
                try:
                    replicas = int(t.split("=", 1)[1])
                except ValueError:
                    pass
                i += 1
            elif t == "--replicas" and i + 1 < len(tokens):
                try:
                    replicas = int(tokens[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                i += 1

        if replicas is None:
            return {"ok": False, "error": "Missing or invalid --replicas for 'scale'.", "raw": raw}

        res = server.scale_deployment(name=deploy_name, namespace=namespace, replicas=replicas)
        return {
            "ok": True,
            "verb": "scale",
            "resource": "deployment",
            "deployment": deploy_name,
            "namespace": namespace,
            "replicas": replicas,
            "raw": raw,
            "result": res,
        }

    return {
        "ok": False,
        "error": f"Unsupported verb: {verb}",
        "raw": raw,
        "hint": "Supported verbs: get, logs, delete, scale.",
    }


if __name__ == "__main__":
    # Run as a local stdio MCP server
    mcp.run(transport="stdio")
