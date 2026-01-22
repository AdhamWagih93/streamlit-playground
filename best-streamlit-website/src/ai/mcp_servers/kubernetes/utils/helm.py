from __future__ import annotations

"""Helm integration helpers for the Kubernetes MCP server.

This module wraps the shared Helm CLI + pyhelm3 utilities under a
kubernetes-specific path so callers no longer need the standalone
``src.ai.mcp_servers.helm`` package. All Helm tools exposed by the
``kubernetes-mcp`` server import from here.
"""

from typing import Any, Dict, List, Optional

from ..config import KubernetesMCPServerConfig
from .helm_config import HelmToolConfig
from .helm_cli import HelmExecConfig as HelmExecConfig
from .helm_cli import get_history as helm_get_history_impl
from .helm_cli import get_manifest as helm_get_manifest_impl
from .helm_cli import get_status as helm_get_status_impl
from .helm_cli import get_values as helm_get_values_impl
from .helm_cli import lint as helm_lint_impl
from .helm_cli import list_releases as helm_list_releases_impl
from .helm_cli import raw as helm_raw_impl
from .helm_cli import repo_add as helm_repo_add_impl
from .helm_cli import repo_list as helm_repo_list_impl
from .helm_cli import repo_update as helm_repo_update_impl
from .helm_cli import search_repo as helm_search_repo_impl
from .helm_cli import template as helm_template_impl
from .helm_cli import uninstall as helm_uninstall_impl
from .helm_cli import upgrade_install as helm_upgrade_install_impl
from .helm_cli import probe_helm_binary as helm_probe_binary
from .pyhelm3_backend import is_available as helm_pyhelm3_available
from .pyhelm3_backend import list_releases as helm_pyhelm3_list_releases
from .pyhelm3_backend import uninstall_release as helm_pyhelm3_uninstall_release


def helm_exec_cfg_from_env() -> HelmExecConfig:
    """Build a HelmExecConfig using the same kubeconfig/context defaults.

    Mirrors the old ``src.ai.mcp_servers.helm.mcp._exec_cfg_from_env`` helper
    but is colocated under the kubernetes MCP namespace.
    """

    helm_cfg = HelmToolConfig.from_env()
    k8s_cfg = KubernetesMCPServerConfig.from_env()
    return HelmExecConfig(
        helm_bin=helm_cfg.helm_bin,
        auto_install=bool(helm_cfg.auto_install),
        auto_install_version=str(helm_cfg.auto_install_version or "v3.14.4"),
        auto_install_dir=helm_cfg.auto_install_dir,
        kubeconfig=k8s_cfg.kubeconfig,
        kubecontext=k8s_cfg.context,
    )


__all__ = [
    "HelmExecConfig",
    "HelmToolConfig",
    "helm_exec_cfg_from_env",
    "helm_get_history_impl",
    "helm_get_manifest_impl",
    "helm_get_status_impl",
    "helm_get_values_impl",
    "helm_lint_impl",
    "helm_list_releases_impl",
    "helm_raw_impl",
    "helm_repo_add_impl",
    "helm_repo_list_impl",
    "helm_repo_update_impl",
    "helm_search_repo_impl",
    "helm_template_impl",
    "helm_uninstall_impl",
    "helm_upgrade_install_impl",
    "helm_probe_binary",
    "helm_pyhelm3_available",
    "helm_pyhelm3_list_releases",
    "helm_pyhelm3_uninstall_release",
]
