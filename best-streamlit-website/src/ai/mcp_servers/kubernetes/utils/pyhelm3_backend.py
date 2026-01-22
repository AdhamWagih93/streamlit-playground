from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional


def _looks_like_kube_unreachable(msg: str) -> bool:
    low = (msg or "").lower()
    return any(
        s in low
        for s in [
            "kubernetes cluster unreachable",
            "http://localhost:8080/version",
            "the connection to the server localhost:8080",
            "dial tcp",
            "getaddrinfow",
        ]
    )


def _kube_hint(*, kubeconfig: Optional[str], kubecontext: Optional[str]) -> str:
    bits = [
        "Local/dev: set K8S_KUBECONFIG (or KUBECONFIG) to a valid kubeconfig path.",
        "Remote/in-cluster: ensure the Pod runs inside Kubernetes and has a ServiceAccount + RBAC.",
        "Remote/out-of-cluster: mount a kubeconfig and set K8S_KUBECONFIG (or KUBECONFIG).",
    ]
    cfg_bits = []
    if kubeconfig:
        cfg_bits.append(f"kubeconfig={kubeconfig}")
    if kubecontext:
        cfg_bits.append(f"kubecontext={kubecontext}")
    if cfg_bits:
        bits.append("Current: " + ", ".join(cfg_bits))
    return " ".join(bits)


def _maybe_import_pyhelm3():
    try:
        from pyhelm3 import Client  # type: ignore

        return Client
    except Exception:
        return None


def is_available() -> bool:
    return _maybe_import_pyhelm3() is not None


def list_releases(
    *,
    helm_executable: str,
    kubeconfig: Optional[str],
    kubecontext: Optional[str],
    namespace: Optional[str],
    all_namespaces: bool,
) -> Dict[str, Any]:
    """List releases via pyhelm3 when available.

    Note: pyhelm3's list API returns objects; we normalise into dict rows.
    """

    Client = _maybe_import_pyhelm3()
    if Client is None:
        return {"ok": False, "error": "pyhelm3 is not installed"}

    async def _run() -> List[Dict[str, Any]]:
        client = Client(kubeconfig=kubeconfig, kubecontext=kubecontext, executable=helm_executable)

        kwargs: Dict[str, Any] = {
            "all": True,
            "all_namespaces": bool(all_namespaces),
        }
        if namespace and not all_namespaces:
            # Best-effort: supported by pyhelm3
            kwargs["namespace"] = namespace

        try:
            releases = await client.list_releases(**kwargs)
        except TypeError:
            # Older/newer signature fallback
            releases = await client.list_releases(all=True, all_namespaces=bool(all_namespaces))

        rows: List[Dict[str, Any]] = []
        for rel in releases or []:
            try:
                rev = await rel.current_revision()
                status = str(getattr(rev, "status", ""))
                revision = getattr(rev, "revision", None)
                chart_str = ""
                app_version = ""
                try:
                    md = await rev.chart_metadata()
                    cname = getattr(md, "name", "")
                    cver = getattr(md, "version", "")
                    chart_str = f"{cname}-{cver}" if cname and cver else (cname or "")
                    app_version = getattr(md, "app_version", "") or ""
                except Exception:
                    pass

                rows.append(
                    {
                        "name": getattr(rel, "name", None),
                        "namespace": getattr(rel, "namespace", None),
                        "revision": revision,
                        "status": status.lower() if status else status,
                        "chart": chart_str,
                        "app_version": app_version,
                    }
                )
            except Exception:
                rows.append(
                    {
                        "name": getattr(rel, "name", None),
                        "namespace": getattr(rel, "namespace", None),
                    }
                )
        return rows

    try:
        rows = asyncio.run(_run())
        return {"ok": True, "releases": rows}
    except RuntimeError:
        # In case an event loop is already running (unlikely in FastMCP tool), create a new task.
        try:
            rows = asyncio.get_event_loop().run_until_complete(_run())
            return {"ok": True, "releases": rows}
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if _looks_like_kube_unreachable(msg):
                return {"ok": False, "error": "Kubernetes cluster unreachable.", "details": msg, "hint": _kube_hint(kubeconfig=kubeconfig, kubecontext=kubecontext)}
            return {"ok": False, "error": msg}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if _looks_like_kube_unreachable(msg):
            return {"ok": False, "error": "Kubernetes cluster unreachable.", "details": msg, "hint": _kube_hint(kubeconfig=kubeconfig, kubecontext=kubecontext)}
        return {"ok": False, "error": msg}


def uninstall_release(
    *,
    helm_executable: str,
    kubeconfig: Optional[str],
    kubecontext: Optional[str],
    release: str,
    namespace: Optional[str],
    wait: bool,
) -> Dict[str, Any]:
    Client = _maybe_import_pyhelm3()
    if Client is None:
        return {"ok": False, "error": "pyhelm3 is not installed"}

    async def _run() -> None:
        client = Client(kubeconfig=kubeconfig, kubecontext=kubecontext, executable=helm_executable)
        kwargs: Dict[str, Any] = {"namespace": namespace, "wait": bool(wait)}
        # pyhelm3 exposes uninstall_release(name, namespace=..., wait=True)
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        await client.uninstall_release(release, **kwargs)

    try:
        asyncio.run(_run())
        return {"ok": True}
    except RuntimeError:
        try:
            asyncio.get_event_loop().run_until_complete(_run())
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if _looks_like_kube_unreachable(msg):
                return {"ok": False, "error": "Kubernetes cluster unreachable.", "details": msg, "hint": _kube_hint(kubeconfig=kubeconfig, kubecontext=kubecontext)}
            return {"ok": False, "error": msg}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if _looks_like_kube_unreachable(msg):
            return {"ok": False, "error": "Kubernetes cluster unreachable.", "details": msg, "hint": _kube_hint(kubeconfig=kubeconfig, kubecontext=kubecontext)}
        return {"ok": False, "error": msg}
