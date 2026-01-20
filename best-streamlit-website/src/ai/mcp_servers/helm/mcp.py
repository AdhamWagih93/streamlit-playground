from __future__ import annotations

import inspect
import os
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from .config import HelmMCPServerConfig
from .utils.helm_cli import HelmExecConfig
from .utils.helm_cli import probe_helm_binary
from .utils.helm_cli import get_history as get_history_impl
from .utils.helm_cli import get_manifest as get_manifest_impl
from .utils.helm_cli import get_status as get_status_impl
from .utils.helm_cli import get_values as get_values_impl
from .utils.helm_cli import lint as lint_impl
from .utils.helm_cli import list_releases as list_releases_impl
from .utils.helm_cli import raw as raw_impl
from .utils.helm_cli import repo_add as repo_add_impl
from .utils.helm_cli import repo_list as repo_list_impl
from .utils.helm_cli import repo_update as repo_update_impl
from .utils.helm_cli import search_repo as search_repo_impl
from .utils.helm_cli import template as template_impl
from .utils.helm_cli import uninstall as uninstall_impl
from .utils.helm_cli import upgrade_install as upgrade_install_impl
from .utils.pyhelm3_backend import is_available as pyhelm3_available
from .utils.pyhelm3_backend import list_releases as pyhelm3_list_releases
from .utils.pyhelm3_backend import uninstall_release as pyhelm3_uninstall_release


mcp = FastMCP("helm-mcp")


def _exec_cfg_from_env() -> HelmExecConfig:
    cfg = HelmMCPServerConfig.from_env()
    return HelmExecConfig(
        helm_bin=cfg.helm_bin,
        auto_install=bool(cfg.auto_install),
        auto_install_version=str(cfg.auto_install_version or "v3.14.4"),
        auto_install_dir=cfg.auto_install_dir,
        kubeconfig=cfg.kubeconfig,
        kubecontext=cfg.kubecontext,
    )


@mcp.tool
def health_check() -> Dict[str, Any]:
    """Quick connectivity check.

    - Verifies Kubernetes API connectivity using the same kubeconfig/context defaults.
    - Reports whether Helm is available (or will auto-install) without invoking Helm.
    """

    cfg_all = HelmMCPServerConfig.from_env()
    exec_cfg = _exec_cfg_from_env()

    kube: Dict[str, Any] = {"ok": False}
    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config

        loaded = False
        # Prefer explicit kubeconfig/context when provided.
        if cfg_all.kubeconfig or cfg_all.kubecontext:
            k8s_config.load_kube_config(config_file=cfg_all.kubeconfig, context=cfg_all.kubecontext)
            loaded = True
        else:
            # Fall back to in-cluster config, else default kubeconfig.
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

    helm_probe = probe_helm_binary(exec_cfg)
    # Provide a quick hint for users who don't want downloads.
    if not helm_probe.get("ok"):
        helm_probe["hint"] = "Set HELM_AUTO_INSTALL=false to disable auto-download, or set HELM_BIN to a helm path."

    return {
        "ok": bool(kube.get("ok")),
        "kubernetes": kube,
        "helm": helm_probe,
        "context": {
            "kubeconfig": cfg_all.kubeconfig,
            "kubecontext": cfg_all.kubecontext,
        },
    }


@mcp.tool
def list_releases(namespace: Optional[str] = None, all_namespaces: bool = True) -> Dict[str, Any]:
    """List Helm releases (defaults to all namespaces)."""

    cfg = _exec_cfg_from_env()
    if pyhelm3_available():
        res = pyhelm3_list_releases(
            helm_executable=cfg.helm_bin,
            kubeconfig=cfg.kubeconfig,
            kubecontext=cfg.kubecontext,
            namespace=namespace,
            all_namespaces=bool(all_namespaces),
        )
        if isinstance(res, dict) and res.get("ok"):
            return res

    return list_releases_impl(cfg, namespace=namespace, all_namespaces=bool(all_namespaces))


@mcp.tool
def get_release_status(release: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    """Get release status (JSON when supported by helm)."""

    cfg = _exec_cfg_from_env()
    return get_status_impl(cfg, release, namespace=namespace)


@mcp.tool
def get_release_history(release: str, namespace: Optional[str] = None, max_entries: int = 20) -> Dict[str, Any]:
    cfg = _exec_cfg_from_env()
    return get_history_impl(cfg, release, namespace=namespace, max_entries=int(max_entries))


@mcp.tool
def get_release_values(release: str, namespace: Optional[str] = None, all_values: bool = False) -> Dict[str, Any]:
    cfg = _exec_cfg_from_env()
    return get_values_impl(cfg, release, namespace=namespace, all_values=bool(all_values))


@mcp.tool
def get_release_manifest(release: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    cfg = _exec_cfg_from_env()
    return get_manifest_impl(cfg, release, namespace=namespace)


@mcp.tool
def uninstall_release(release: str, namespace: Optional[str] = None, keep_history: bool = False, wait: bool = True, timeout: str = "5m") -> Dict[str, Any]:
    cfg = _exec_cfg_from_env()

    # Prefer pyhelm3 for uninstall when available (Helm 3, tiller-less).
    if pyhelm3_available() and not keep_history:
        try:
            return pyhelm3_uninstall_release(
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

    return uninstall_impl(
        cfg,
        release,
        namespace=namespace,
        keep_history=bool(keep_history),
        wait=bool(wait),
        timeout=str(timeout or "5m"),
    )


@mcp.tool
def upgrade_install_release(
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
    """Upgrade or install a release.

    - values_yaml: inline values as YAML (passed via stdin)
    - values_files: list of file paths available within the helm-mcp container
    - set_values: dict converted into repeated `--set key=value`

    Note: values_files must refer to paths on the server side.
    """

    cfg = _exec_cfg_from_env()
    return upgrade_install_impl(
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
def repo_list() -> Dict[str, Any]:
    cfg = _exec_cfg_from_env()
    return repo_list_impl(cfg)


@mcp.tool
def repo_add(name: str, url: str, username: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
    cfg = _exec_cfg_from_env()
    return repo_add_impl(cfg, name=name, url=url, username=username, password=password)


@mcp.tool
def repo_update() -> Dict[str, Any]:
    cfg = _exec_cfg_from_env()
    return repo_update_impl(cfg)


@mcp.tool
def search_repo(query: str, versions: bool = False) -> Dict[str, Any]:
    cfg = _exec_cfg_from_env()
    return search_repo_impl(cfg, query=query, versions=bool(versions))


@mcp.tool
def lint_chart(chart: str, values_yaml: Optional[str] = None) -> Dict[str, Any]:
    cfg = _exec_cfg_from_env()
    return lint_impl(cfg, chart=chart, values_yaml=values_yaml)


@mcp.tool
def template_chart(release: str, chart: str, namespace: Optional[str] = None, values_yaml: Optional[str] = None) -> Dict[str, Any]:
    cfg = _exec_cfg_from_env()
    return template_impl(cfg, release=release, chart=chart, namespace=namespace, values_yaml=values_yaml)


@mcp.tool
def helm_raw(args: List[str]) -> Dict[str, Any]:
    """Run an arbitrary helm subcommand.

    Disabled by default. Enable with HELM_ALLOW_RAW=true.
    """

    cfg_all = HelmMCPServerConfig.from_env()
    if not cfg_all.allow_raw:
        return {
            "ok": False,
            "error": "helm_raw is disabled. Set HELM_ALLOW_RAW=true on the server to enable.",
        }

    if not args:
        return {"ok": False, "error": "args cannot be empty"}

    cfg = _exec_cfg_from_env()
    return raw_impl(cfg, args=list(args))


def run_stdio() -> None:
    cfg = HelmMCPServerConfig.from_env()
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
