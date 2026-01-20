from __future__ import annotations

import shlex
from typing import Any, Dict, Optional, Tuple

from .clients import KubernetesClientSet
from .formatting import to_json_text, to_yaml_text


def kubectl_like(clients: KubernetesClientSet, command: str) -> Dict[str, Any]:
    """Parse and execute a limited, safe subset of kubectl-style commands."""

    raw = command.strip()
    if not raw:
        return {
            "ok": False,
            "error": "Empty command.",
            "hint": "Examples: 'get pods -n default', 'get sa -n kube-system', 'logs my-pod -n default --tail=200'",
        }

    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        return {"ok": False, "error": f"Could not parse command: {exc}", "raw": raw}

    idx = 0
    if tokens and tokens[0] == "kubectl":
        idx += 1
    if idx >= len(tokens):
        return {"ok": False, "error": "Missing verb.", "raw": raw}

    verb = tokens[idx]
    idx += 1

    def _parse_output(start: int) -> str:
        out = "table"
        i = start
        while i < len(tokens):
            t = tokens[i]
            if t in ("-o", "--output") and i + 1 < len(tokens):
                out = tokens[i + 1].strip()
                i += 2
                continue
            if t.startswith("-o="):
                out = t.split("=", 1)[1].strip()
                i += 1
                continue
            i += 1
        return out or "table"

    def _parse_namespace(start: int) -> Tuple[Optional[str], int]:
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

    def _parse_all_namespaces(start: int) -> bool:
        i = start
        while i < len(tokens):
            t = tokens[i]
            if t in ("-A", "--all-namespaces"):
                return True
            i += 1
        return False

    if verb == "get":
        if idx >= len(tokens):
            return {"ok": False, "error": "Missing resource for 'get'.", "raw": raw}
        resource = tokens[idx]
        idx += 1
        ns, _ = _parse_namespace(idx)
        all_namespaces = _parse_all_namespaces(idx)
        output = _parse_output(idx)

        if resource in ("pods", "po"):
            from .core_resources import list_pods

            effective_ns = None if all_namespaces else ns
            res = list_pods(clients.core, namespace=effective_ns)
            return _wrap_get_result(raw, "pods", res, namespace=effective_ns, output=output)
        if resource in ("nodes", "no"):
            from .core_resources import list_nodes

            res = list_nodes(clients.core)
            return _wrap_get_result(raw, "nodes", res, output=output)
        if resource in ("namespaces", "ns"):
            from .core_resources import list_namespaces

            res = list_namespaces(clients.core)
            return _wrap_get_result(raw, "namespaces", res, output=output)
        if resource in ("deployments", "deploy", "deployment"):
            if all_namespaces:
                from .workloads import list_deployments_all

                res = list_deployments_all(clients.apps)
                ns = None
            else:
                ns = ns or "default"
                from .workloads import list_deployments

                res = list_deployments(clients.apps, namespace=ns)
            return _wrap_get_result(raw, "deployments", res, namespace=ns, output=output)
        if resource in ("services", "svc", "service"):
            if all_namespaces:
                from .core_resources import list_services_all

                res = list_services_all(clients.core)
                ns = None
            else:
                ns = ns or "default"
                from .core_resources import list_services

                res = list_services(clients.core, namespace=ns)
            return _wrap_get_result(raw, "services", res, namespace=ns, output=output)
        if resource in ("events", "ev"):
            if all_namespaces:
                from .core_resources import list_events_all

                res = list_events_all(clients.core)
                ns = None
            else:
                ns = ns or "default"
                from .core_resources import list_events

                res = list_events(clients.core, namespace=ns)
            return _wrap_get_result(raw, "events", res, namespace=ns, output=output)
        if resource in ("serviceaccounts", "serviceaccount", "sa"):
            if all_namespaces:
                from .access_mgmt import list_service_accounts_all

                res = list_service_accounts_all(clients.core)
                ns = None
            else:
                ns = ns or "default"
                from .access_mgmt import list_service_accounts

                res = list_service_accounts(clients.core, namespace=ns)
            return _wrap_get_result(raw, "service_accounts", res, namespace=ns, output=output)

        return {
            "ok": False,
            "error": f"Unsupported resource for 'get': {resource}",
            "raw": raw,
            "hint": "Supported: pods, nodes, namespaces, deployments, services, events, sa.",
        }

    if verb == "create":
        if idx >= len(tokens):
            return {"ok": False, "error": "Missing resource for 'create'.", "raw": raw}
        resource = tokens[idx]
        idx += 1

        if resource in ("namespace", "namespaces", "ns"):
            if idx >= len(tokens):
                return {"ok": False, "error": "Missing name for 'create namespace'.", "raw": raw}
            name = tokens[idx]
            from .core_resources import create_namespace

            res = create_namespace(clients.core, name=name)
            ok = bool(isinstance(res, dict) and res.get("ok"))
            out: Dict[str, Any] = {"ok": ok, "verb": "create", "resource": "namespace", "name": name, "raw": raw, "result": res}
            if not ok and isinstance(res, dict):
                out["error"] = res.get("error")
            return out

        return {
            "ok": False,
            "error": f"Unsupported resource for 'create': {resource}",
            "raw": raw,
            "hint": "Supported: create namespace <name>",
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

        from .core_resources import get_pod_logs

        res = get_pod_logs(clients.core, name=pod_name, namespace=namespace, tail_lines=tail_lines)
        text = res.get("logs") if isinstance(res, dict) and res.get("ok") else None
        return {
            "ok": True,
            "verb": "logs",
            "pod": pod_name,
            "namespace": namespace,
            "tail_lines": tail_lines,
            "raw": raw,
            "result": res,
            "output": "text",
            "text": text or "",
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
        from .core_resources import delete_pod

        res = delete_pod(clients.core, name=pod_name, namespace=namespace)
        return {"ok": True, "verb": "delete", "resource": "pod", "pod": pod_name, "namespace": namespace, "raw": raw, "result": res}

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

        from .workloads import scale_deployment

        res = scale_deployment(clients.apps, name=deploy_name, namespace=namespace, replicas=replicas)
        return {"ok": True, "verb": "scale", "resource": "deployment", "deployment": deploy_name, "namespace": namespace, "replicas": replicas, "raw": raw, "result": res}

    return {
        "ok": False,
        "error": f"Unsupported verb: {verb}",
        "raw": raw,
        "hint": "Supported verbs: get, logs, delete, scale, create.",
    }


def _wrap_get_result(
    raw: str,
    resource: str,
    result: Dict[str, Any],
    *,
    namespace: Optional[str] = None,
    output: str = "table",
) -> Dict[str, Any]:
    payload: Any = result
    if isinstance(result, dict):
        # Prefer the main list key if present.
        for key in (
            "pods",
            "nodes",
            "namespaces",
            "deployments",
            "services",
            "events",
            "service_accounts",
        ):
            if key in result:
                payload = result.get(key)
                break

    text = ""
    fmt = (output or "table").lower()
    if fmt in ("yaml", "yml"):
        text = to_yaml_text(payload)
    elif fmt == "json":
        text = to_json_text(payload)
    elif fmt in ("wide", "table"):
        text = ""
    else:
        # Unknown output format; return something useful.
        text = to_json_text(payload)
        fmt = "json"

    out: Dict[str, Any] = {
        "ok": True,
        "verb": "get",
        "resource": resource,
        "namespace": namespace,
        "raw": raw,
        "result": result,
        "output": fmt,
    }
    if text:
        out["text"] = text
    return out
