from __future__ import annotations

from typing import Any, Dict, List

from kubernetes.client import ApiException


def service_account_to_dict(sa: Any) -> Dict[str, Any]:
    return {
        "name": sa.metadata.name,
        "namespace": sa.metadata.namespace,
        "secrets": [getattr(s, "name", None) for s in (getattr(sa, "secrets", None) or [])],
        "imagePullSecrets": [getattr(s, "name", None) for s in (getattr(sa, "image_pull_secrets", None) or [])],
    }


def map_service_accounts(items: List[Any]) -> List[Dict[str, Any]]:
    return [service_account_to_dict(sa) for sa in items]


def list_service_accounts(core_api: Any, namespace: str = "default") -> Dict[str, Any]:
    try:
        items = core_api.list_namespaced_service_account(namespace=namespace).items
    except ApiException as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "service_accounts": map_service_accounts(items)}


def list_service_accounts_all(core_api: Any) -> Dict[str, Any]:
    try:
        items = core_api.list_service_account_for_all_namespaces().items
    except ApiException as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "service_accounts": map_service_accounts(items)}
