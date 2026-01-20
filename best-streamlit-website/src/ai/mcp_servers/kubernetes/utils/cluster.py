from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Optional

from kubernetes.client import ApiException


def health_check(core_api: Any, version_api: Any) -> Dict[str, Any]:
    """Basic reachability and API responsiveness checks."""

    checks: List[Dict[str, Any]] = []

    def _run(name: str, fn) -> None:
        started = perf_counter()
        try:
            fn()
            checks.append({"name": name, "ok": True, "ms": int((perf_counter() - started) * 1000)})
        except ApiException as exc:
            checks.append({"name": name, "ok": False, "ms": int((perf_counter() - started) * 1000), "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": name, "ok": False, "ms": int((perf_counter() - started) * 1000), "error": str(exc)})

    version_info: Dict[str, Any] | None = None
    try:
        started = perf_counter()
        v = version_api.get_code()
        version_info = {
            "major": getattr(v, "major", None),
            "minor": getattr(v, "minor", None),
            "gitVersion": getattr(v, "git_version", None),
            "platform": getattr(v, "platform", None),
            "ms": int((perf_counter() - started) * 1000),
        }
        checks.append({"name": "version", "ok": True, "ms": version_info["ms"]})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "version", "ok": False, "error": str(exc)})

    _run("namespaces", lambda: core_api.list_namespace(limit=1))
    _run("nodes", lambda: core_api.list_node(limit=1))

    ok = any(c.get("ok") for c in checks)
    return {
        "ok": ok,
        "reachable": ok,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "version": version_info,
        "checks": checks,
    }


def get_cluster_stats(core_api: Any, apps_api: Any) -> Dict[str, Any]:
    """High-level counts for common resource types; RBAC-resilient."""

    counts: Dict[str, Optional[int]] = {
        "nodes": None,
        "namespaces": None,
        "pods": None,
        "deployments": None,
        "services": None,
        "serviceAccounts": None,
    }
    errors: List[Dict[str, Any]] = []

    def _count(area: str, fn) -> None:
        try:
            counts[area] = len(fn().items)
        except ApiException as exc:
            errors.append({"area": area, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            errors.append({"area": area, "error": str(exc)})

    _count("nodes", core_api.list_node)
    _count("namespaces", core_api.list_namespace)
    _count("pods", core_api.list_pod_for_all_namespaces)
    _count("deployments", apps_api.list_deployment_for_all_namespaces)
    _count("services", core_api.list_service_for_all_namespaces)
    _count("serviceAccounts", core_api.list_service_account_for_all_namespaces)

    ok = any(v is not None for v in counts.values())
    out: Dict[str, Any] = {
        "ok": ok,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
    }
    if errors:
        out["errors"] = errors
    return out


def get_cluster_overview(core_api: Any) -> Dict[str, Any]:
    """Back-compat overview used by the UI; RBAC-resilient."""

    nodes_count: int | None = None
    namespaces_count: int | None = None
    pods_count: int | None = None
    errors: List[Dict[str, Any]] = []

    try:
        nodes_count = len(core_api.list_node().items)
    except ApiException as exc:
        errors.append({"area": "nodes", "error": str(exc)})

    try:
        namespaces_count = len(core_api.list_namespace().items)
    except ApiException as exc:
        errors.append({"area": "namespaces", "error": str(exc)})

    try:
        pods_count = len(core_api.list_pod_for_all_namespaces().items)
    except ApiException as exc:
        errors.append({"area": "pods", "error": str(exc)})

    ok = any(v is not None for v in (nodes_count, namespaces_count, pods_count))
    result: Dict[str, Any] = {"ok": ok, "nodes": nodes_count, "namespaces": namespaces_count, "pods": pods_count}
    if errors:
        result["errors"] = errors
    return result
