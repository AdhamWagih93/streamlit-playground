from __future__ import annotations

import inspect
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

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

# Helm integration: re-expose Helm tools on the kubernetes-mcp server so
# callers can reach both Kubernetes and Helm via a single MCP endpoint
from .utils.helm import (
    HelmExecConfig,
    HelmToolConfig,
    helm_exec_cfg_from_env,
    helm_get_history_impl,
    helm_get_manifest_impl,
    helm_get_status_impl,
    helm_get_values_impl,
    helm_lint_impl,
    helm_list_releases_impl,
    helm_probe_binary,
    helm_pyhelm3_available,
    helm_pyhelm3_list_releases,
    helm_pyhelm3_uninstall_release,
    helm_raw_impl,
    helm_repo_add_impl,
    helm_repo_list_impl,
    helm_repo_update_impl,
    helm_search_repo_impl,
    helm_template_impl,
    helm_uninstall_impl,
    helm_upgrade_install_impl,
)


mcp = FastMCP("kubernetes-mcp")

_CLIENTS: Optional[KubernetesClientSet] = None


def _clients_from_env() -> KubernetesClientSet:
    global _CLIENTS
    if _CLIENTS is not None:
        return _CLIENTS

    cfg = KubernetesMCPServerConfig.from_env()
    _CLIENTS = load_clients(kubeconfig=cfg.kubeconfig, context=cfg.context)
    return _CLIENTS


def _repo_root() -> Path:
    # .../best-streamlit-website/src/ai/mcp_servers/kubernetes/mcp.py
    return Path(__file__).resolve().parents[4]


def _validate_manifest_path(file_path: str) -> tuple[Optional[Path], Optional[Dict[str, Any]]]:
    p_raw = (file_path or "").strip()
    if not p_raw:
        return None, {"ok": False, "error": "file_path is required"}

    try:
        p = Path(p_raw).expanduser().resolve()
    except Exception:
        return None, {"ok": False, "error": f"Invalid file_path: {file_path}"}

    root = _repo_root().resolve()
    allowed_dir = (root / "deploy" / "k8s").resolve()

    # Allow only files under deploy/k8s
    try:
        p.relative_to(allowed_dir)
    except Exception:
        return None, {"ok": False, "error": "file_path must be under deploy/k8s", "allowed_dir": str(allowed_dir)}

    if not p.is_file():
        return None, {"ok": False, "error": f"Manifest file not found: {str(p)}"}

    return p, None


def _kubectl_or_error() -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    kubectl = shutil.which("kubectl")
    if not kubectl:
        return None, {"ok": False, "error": "kubectl not found in PATH", "hint": "Install kubectl or ensure it is on PATH."}
    return kubectl, None


@mcp.tool
def kubectl_apply(file_path: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    """Apply a local manifest under deploy/k8s using kubectl.

    This is intended for Streamlit's Setup 'Using Kubernetes' flow.
    """

    p, err = _validate_manifest_path(file_path)
    if err:
        return err

    kubectl, kerr = _kubectl_or_error()
    if kerr:
        return kerr

    cmd = [kubectl, "apply", "-f", str(p)]
    ns = (namespace or "").strip()
    if ns:
        cmd += ["-n", ns]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "command": cmd}

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": "kubectl_apply_failed",
            "command": cmd,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "exit_code": int(proc.returncode),
        }
    return {"ok": True, "command": cmd, "stdout": (proc.stdout or "").strip()}


@mcp.tool
def kubectl_delete(file_path: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    """Delete resources in a local manifest under deploy/k8s using kubectl."""

    p, err = _validate_manifest_path(file_path)
    if err:
        return err

    kubectl, kerr = _kubectl_or_error()
    if kerr:
        return kerr

    cmd = [kubectl, "delete", "-f", str(p)]
    ns = (namespace or "").strip()
    if ns:
        cmd += ["-n", ns]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "command": cmd}

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": "kubectl_delete_failed",
            "command": cmd,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "exit_code": int(proc.returncode),
        }
    return {"ok": True, "command": cmd, "stdout": (proc.stdout or "").strip()}


def _auth_or_error(_client_token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate client token for MCP tools.

    Mirrors the Jenkins MCP auth pattern but uses KUBERNETES_MCP_CLIENT_TOKEN
    / DEFAULT_KUBERNETES_MCP_CLIENT_TOKEN semantics from the config layer.
    """

    # For now we keep a simple env-based check; if no token is configured,
    # tools remain open (useful for local dev).
    expected = os.environ.get("KUBERNETES_MCP_CLIENT_TOKEN")
    if not expected:
        return None
    if _client_token != expected:
        return {"ok": False, "error": "unauthorized", "hint": "Invalid or missing client token."}
    return None


def _helm_exec_cfg_from_env() -> HelmExecConfig:
    """Back-compat wrapper for Helm exec config.

    The Helm tools in this module historically called a private helper named
    ``_helm_exec_cfg_from_env``. The actual implementation lives in
    ``src.ai.mcp_servers.kubernetes.utils.helm.helm_exec_cfg_from_env``.
    """

    return helm_exec_cfg_from_env()



@mcp.tool
def health_check(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Run basic reachability and API responsiveness checks."""
    err = _auth_or_error(_client_token)
    if err:
        return err
    c = _clients_from_env()
    return health_check_impl(c.core, c.version)


@mcp.tool
def get_cluster_stats(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return high-level counts for common resource types."""
    err = _auth_or_error(_client_token)
    if err:
        return err
    c = _clients_from_env()
    return get_cluster_stats_impl(c.core, c.apps)


@mcp.tool
def get_cluster_overview(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Back-compat overview (nodes, namespaces, pods)."""
    err = _auth_or_error(_client_token)
    if err:
        return err
    c = _clients_from_env()
    return get_cluster_overview_impl(c.core)


@mcp.tool
def list_namespaces(_client_token: Optional[str] = None) -> Dict[str, Any]:
    err = _auth_or_error(_client_token)
    if err:
        return err
    c = _clients_from_env()
    return list_namespaces_impl(c.core)


@mcp.tool
def list_nodes(_client_token: Optional[str] = None) -> Dict[str, Any]:
    err = _auth_or_error(_client_token)
    if err:
        return err
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


# -------------------------- Helm tools (via kubernetes-mcp) ---------------------------


@mcp.tool
def helm_health_check() -> Dict[str, Any]:
    """Helm connectivity + context check.

    - Verifies Kubernetes API reachability using the same kubeconfig/context
      that Helm would use.
    - Probes the Helm binary (and auto-install, if enabled).
    """

    helm_cfg = HelmToolConfig.from_env()
    k8s_cfg = KubernetesMCPServerConfig.from_env()
    exec_cfg = helm_exec_cfg_from_env()

    kube: Dict[str, Any] = {"ok": False}
    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config

        loaded = False
        if k8s_cfg.kubeconfig or k8s_cfg.context:
            k8s_config.load_kube_config(config_file=k8s_cfg.kubeconfig, context=k8s_cfg.context)
            loaded = True
        else:
            try:
                k8s_config.load_incluster_config()
                loaded = True
            except Exception:
                k8s_config.load_kube_config()
                loaded = True

        v = k8s_client.VersionApi().get_code()
        kube = {
            "ok": True,
            "loaded": bool(loaded),
            "git_version": getattr(v, "git_version", None),
            "platform": getattr(v, "platform", None),
        }
    except Exception as exc:  # noqa: BLE001
        kube = {"ok": False, "error": str(exc)}

    helm_probe = helm_probe_binary(exec_cfg)
    if not helm_probe.get("ok"):
        helm_probe["hint"] = (
            "Set HELM_AUTO_INSTALL=false to disable auto-download, "
            "or set HELM_BIN to an existing helm path."
        )

    return {
        "ok": bool(kube.get("ok")),
        "kubernetes": kube,
        "helm": helm_probe,
        "context": {
            "kubeconfig": k8s_cfg.kubeconfig,
            "kubecontext": k8s_cfg.context,
        },
        "config": {
            "helm_allow_raw": bool(helm_cfg.allow_raw),
        },
    }


@mcp.tool
def helm_list_releases(namespace: Optional[str] = None, all_namespaces: bool = True) -> Dict[str, Any]:
    """List Helm releases (defaults to all namespaces)."""

    def _looks_like_winsock_provider_error(text: str) -> bool:
        t = (text or "").lower()
        return (
            "service provider could not be loaded or initialized" in t
            or "wsaproviderfailedinit" in t
            or "requested service provider could not be loaded" in t
            or "provider could not be loaded" in t
        )

    def _list_releases_via_secrets(
        *,
        ns: Optional[str],
        all_ns: bool,
    ) -> Dict[str, Any]:
        """Best-effort Helm release listing without calling Helm.

        Helm v3 stores release records as Secrets with labels like:
          owner=helm, name=<release>, status=<status>, version=<revision>

        This fallback keeps the UI working when the Helm binary can't connect
        (e.g., Windows Winsock provider errors).
        """

        c = _clients_from_env()
        label_selector = "owner=helm"

        if all_ns:
            sec_list = c.core.list_secret_for_all_namespaces(label_selector=label_selector)
        else:
            sec_list = c.core.list_namespaced_secret(namespace=ns or "default", label_selector=label_selector)

        # Pick latest revision per (namespace, release)
        latest: Dict[tuple[str, str], Dict[str, Any]] = {}

        for s in getattr(sec_list, "items", []) or []:
            meta = getattr(s, "metadata", None)
            if not meta:
                continue

            labels = getattr(meta, "labels", None) or {}
            release_name = labels.get("name")
            status = labels.get("status")
            ver = labels.get("version")

            # Only Helm v3 secrets
            sec_type = getattr(s, "type", None)
            sec_name = getattr(meta, "name", "") or ""
            if sec_type and str(sec_type) != "helm.sh/release.v1":
                # Still allow if the name matches the Helm v3 convention.
                if not sec_name.startswith("sh.helm.release.v1."):
                    continue

            # Parse name from secret name if missing
            if not release_name and sec_name.startswith("sh.helm.release.v1."):
                # sh.helm.release.v1.<release>.v<rev>
                m = re.match(r"^sh\.helm\.release\.v1\.(?P<rel>.+)\.v(?P<rev>\d+)$", sec_name)
                if m:
                    release_name = m.group("rel")
                    if not ver:
                        ver = m.group("rev")

            if not release_name:
                continue

            ns_name = getattr(meta, "namespace", None) or "default"
            key = (str(ns_name), str(release_name))

            try:
                rev_i = int(ver) if ver is not None else 0
            except Exception:
                rev_i = 0

            updated = None
            try:
                ts = getattr(meta, "creation_timestamp", None)
                updated = ts.isoformat() if ts else None
            except Exception:
                updated = None

            entry = {
                "name": str(release_name),
                "namespace": str(ns_name),
                "revision": rev_i,
                "status": str(status) if status is not None else None,
                "updated": updated,
                "chart": None,
                "app_version": None,
            }

            prev = latest.get(key)
            if not prev or int(prev.get("revision") or 0) < rev_i:
                latest[key] = entry

        releases = sorted(latest.values(), key=lambda r: (str(r.get("namespace") or ""), str(r.get("name") or "")))
        return {"ok": True, "releases": releases, "backend": "k8s-secrets-fallback"}

    cfg = helm_exec_cfg_from_env()
    if helm_pyhelm3_available():
        res = helm_pyhelm3_list_releases(
            helm_executable=cfg.helm_bin,
            kubeconfig=cfg.kubeconfig,
            kubecontext=cfg.kubecontext,
            namespace=namespace,
            all_namespaces=bool(all_namespaces),
        )
        if isinstance(res, dict) and res.get("ok"):
            return res

    res = helm_list_releases_impl(cfg, namespace=namespace, all_namespaces=bool(all_namespaces))
    if isinstance(res, dict) and not res.get("ok"):
        details = str(res.get("details") or res.get("error") or "")
        if _looks_like_winsock_provider_error(details):
            try:
                fallback = _list_releases_via_secrets(ns=namespace, all_ns=bool(all_namespaces))
                if isinstance(fallback, dict) and fallback.get("ok"):
                    fallback["note"] = "Helm CLI failed with a Windows socket provider error; listed releases via Kubernetes Secrets instead."
                    fallback["helm_cli_error"] = res
                    return fallback
            except Exception as exc:  # noqa: BLE001
                res["fallback_error"] = str(exc)

    return res


@mcp.tool
def helm_get_release_status(release: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    """Get release status (JSON when supported by helm)."""

    cfg = helm_exec_cfg_from_env()
    return helm_get_status_impl(cfg, release, namespace=namespace)


@mcp.tool
def helm_get_release_history(release: str, namespace: Optional[str] = None, max_entries: int = 20) -> Dict[str, Any]:
    cfg = helm_exec_cfg_from_env()
    return helm_get_history_impl(cfg, release, namespace=namespace, max_entries=int(max_entries))


@mcp.tool
def helm_get_release_values(release: str, namespace: Optional[str] = None, all_values: bool = False) -> Dict[str, Any]:
    cfg = helm_exec_cfg_from_env()
    return helm_get_values_impl(cfg, release, namespace=namespace, all_values=bool(all_values))


@mcp.tool
def helm_get_release_manifest(release: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    cfg = helm_exec_cfg_from_env()
    return helm_get_manifest_impl(cfg, release, namespace=namespace)


@mcp.tool
def helm_uninstall_release(
    release: str,
    namespace: Optional[str] = None,
    keep_history: bool = False,
    wait: bool = True,
    timeout: str = "5m",
) -> Dict[str, Any]:
    """Uninstall a Helm release.

    Uses pyhelm3 when available (Helm 3, tiller-less) and keep_history is false,
    otherwise falls back to the Helm CLI backend.
    """

    cfg = helm_exec_cfg_from_env()

    if helm_pyhelm3_available() and not keep_history:
        try:
            return helm_pyhelm3_uninstall_release(
                helm_executable=cfg.helm_bin,
                kubeconfig=cfg.kubeconfig,
                kubecontext=cfg.kubecontext,
                release=release,
                namespace=namespace,
                wait=bool(wait),
            )
        except Exception:
            # fall back to helm CLI
            pass

    return helm_uninstall_impl(
        cfg,
        release,
        namespace=namespace,
        keep_history=bool(keep_history),
        wait=bool(wait),
        timeout=str(timeout or "5m"),
    )


@mcp.tool
def helm_upgrade_install_release(
    release: str,
    chart: str,
    namespace: Optional[str] = None,
    create_namespace: bool = True,
    version: Optional[str] = None,
    values_yaml: Optional[str] = None,
    values_files: Optional[List[str]] = None,
    set_values: Optional[Dict[str, Any]] = None,
    wait: bool = True,
    atomic: bool = False,
    timeout: str = "10m",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Upgrade or install a Helm release.

    - values_yaml: inline values as YAML (passed via stdin)
    - values_files: list of file paths available within the server
    - set_values: dict converted into repeated `--set key=value`
    """

    cfg = helm_exec_cfg_from_env()
    return helm_upgrade_install_impl(
        cfg,
        release,
        chart,
        namespace=namespace,
        create_namespace=bool(create_namespace),
        version=version,
        values_yaml=values_yaml,
        values_files=list(values_files or []),
        set_values=dict(set_values or {}),
        wait=bool(wait),
        atomic=bool(atomic),
        timeout=str(timeout or "10m"),
        dry_run=bool(dry_run),
    )


@mcp.tool
def helm_repo_list() -> Dict[str, Any]:
    cfg = helm_exec_cfg_from_env()
    return helm_repo_list_impl(cfg)


@mcp.tool
def helm_repo_add(name: str, url: str, username: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
    cfg = helm_exec_cfg_from_env()
    return helm_repo_add_impl(cfg, name=name, url=url, username=username, password=password)


@mcp.tool
def helm_repo_update() -> Dict[str, Any]:
    cfg = helm_exec_cfg_from_env()
    return helm_repo_update_impl(cfg)


@mcp.tool
def helm_search_repo(query: str, versions: bool = False) -> Dict[str, Any]:
    cfg = helm_exec_cfg_from_env()
    return helm_search_repo_impl(cfg, query=query, versions=bool(versions))


@mcp.tool
def helm_lint_chart(chart: str, values_yaml: Optional[str] = None) -> Dict[str, Any]:
    cfg = helm_exec_cfg_from_env()
    return helm_lint_impl(cfg, chart=chart, values_yaml=values_yaml)


@mcp.tool
def helm_template_chart(
    release: str,
    chart: str,
    namespace: Optional[str] = None,
    values_yaml: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = helm_exec_cfg_from_env()
    return helm_template_impl(cfg, release=release, chart=chart, namespace=namespace, values_yaml=values_yaml)


@mcp.tool
def helm_raw(args: List[str]) -> Dict[str, Any]:
    """Run an arbitrary helm subcommand when HELM_ALLOW_RAW=true."""

    cfg_all = HelmToolConfig.from_env()
    if not cfg_all.allow_raw:
        return {
            "ok": False,
            "error": "helm_raw is disabled. Set HELM_ALLOW_RAW=true to enable this tool.",
        }

    cfg = helm_exec_cfg_from_env()
    return helm_raw_impl(cfg, args=args)


def run_stdio() -> None:
    """Run the Kubernetes MCP server over HTTP.

    The function name is kept for backwards compatibility with existing
    entrypoints, but the server no longer supports stdio transport.
    """

    cfg = KubernetesMCPServerConfig.from_env()

    host = os.environ.get("MCP_HOST") or cfg.mcp_host
    port_raw = os.environ.get("MCP_PORT")
    try:
        port = int(port_raw) if port_raw else int(cfg.mcp_port)
    except Exception:
        port = int(cfg.mcp_port)

    try:
        mcp.run(transport="http", host=host, port=port)
    except TypeError:
        mcp.run(transport="http")


if __name__ == "__main__":
    run_stdio()
