from __future__ import annotations

from typing import Any, Dict, List, Optional

from kubernetes.client import ApiException


def list_namespaces(core_api: Any) -> Dict[str, Any]:
    try:
        items = core_api.list_namespace().items
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


def list_nodes(core_api: Any) -> Dict[str, Any]:
    try:
        items = core_api.list_node().items
    except ApiException as exc:
        return {"ok": False, "error": str(exc)}

    out: List[Dict[str, Any]] = []
    for node in items:
        capacity = node.status.capacity or {}
        conditions = [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (node.status.conditions or [])
        ]
        out.append({"name": node.metadata.name, "labels": node.metadata.labels or {}, "capacity": capacity, "conditions": conditions})
    return {"ok": True, "nodes": out}


def list_pods(core_api: Any, namespace: Optional[str] = None) -> Dict[str, Any]:
    try:
        if namespace:
            items = core_api.list_namespaced_pod(namespace=namespace).items
        else:
            items = core_api.list_pod_for_all_namespaces().items
    except ApiException as exc:
        return {"ok": False, "error": str(exc)}

    out: List[Dict[str, Any]] = []
    for p in items:
        status = p.status

        container_statuses = getattr(status, "container_statuses", None) or []
        restarts = 0
        ready_containers = 0
        for cs in container_statuses:
            restarts += int(getattr(cs, "restart_count", 0) or 0)
            if getattr(cs, "ready", False):
                ready_containers += 1

        total_containers = len(getattr(p.spec, "containers", None) or [])
        out.append(
            {
                "name": p.metadata.name,
                "namespace": p.metadata.namespace,
                "phase": getattr(status, "phase", None),
                "node": getattr(p.spec, "node_name", None),
                "hostIP": getattr(status, "host_ip", None),
                "podIP": getattr(status, "pod_ip", None),
                "startTime": status.start_time.isoformat() if status.start_time else None,
                "containers": [c.name for c in (p.spec.containers or [])],
                "ready": f"{ready_containers}/{total_containers}",
                "restarts": restarts,
            }
        )
    return {"ok": True, "pods": out}


def get_pod_logs(core_api: Any, name: str, namespace: str = "default", tail_lines: int = 200) -> Dict[str, Any]:
    try:
        log_text = core_api.read_namespaced_pod_log(name=name, namespace=namespace, tail_lines=tail_lines)
    except ApiException as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "logs": log_text}


def delete_pod(core_api: Any, name: str, namespace: str = "default") -> Dict[str, Any]:
    try:
        core_api.delete_namespaced_pod(name=name, namespace=namespace)
    except ApiException as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "name": name, "namespace": namespace}


def list_services(core_api: Any, namespace: str = "default") -> Dict[str, Any]:
    try:
        items = core_api.list_namespaced_service(namespace=namespace).items
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


def list_services_all(core_api: Any) -> Dict[str, Any]:
    try:
        items = core_api.list_service_for_all_namespaces().items
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


def list_events(core_api: Any, namespace: str = "default", limit: int = 200) -> Dict[str, Any]:
    try:
        items = core_api.list_namespaced_event(namespace=namespace).items
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


def list_events_all(core_api: Any, limit: int = 200) -> Dict[str, Any]:
    try:
        items = core_api.list_event_for_all_namespaces().items
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
