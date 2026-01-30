"""Kubernetes client factory and auth utilities."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from ...config import KubernetesMCPServerConfig
from ...utils.clients import KubernetesClientSet, load_clients


_CLIENTS: Optional[KubernetesClientSet] = None


def clients_from_env() -> KubernetesClientSet:
    """Get or create a KubernetesClientSet from environment configuration."""
    global _CLIENTS
    if _CLIENTS is not None:
        return _CLIENTS

    cfg = KubernetesMCPServerConfig.from_env()
    _CLIENTS = load_clients(kubeconfig=cfg.kubeconfig, context=cfg.context)
    return _CLIENTS


def auth_or_error(_client_token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate client token for MCP tools.

    Mirrors the Jenkins MCP auth pattern but uses KUBERNETES_MCP_CLIENT_TOKEN
    / DEFAULT_KUBERNETES_MCP_CLIENT_TOKEN semantics from the config layer.
    """
    expected = os.environ.get("KUBERNETES_MCP_CLIENT_TOKEN")
    if not expected:
        return None
    if _client_token != expected:
        return {"ok": False, "error": "unauthorized", "hint": "Invalid or missing client token."}
    return None


def repo_root() -> Path:
    """Get the repository root path."""
    # .../best-streamlit-website/src/ai/mcp_servers/kubernetes/components/tools/client_factory.py
    return Path(__file__).resolve().parents[6]


def validate_manifest_path(file_path: str) -> tuple[Optional[Path], Optional[Dict[str, Any]]]:
    """Validate that a manifest path is under the allowed deploy/k8s directory."""
    p_raw = (file_path or "").strip()
    if not p_raw:
        return None, {"ok": False, "error": "file_path is required"}

    try:
        p = Path(p_raw).expanduser().resolve()
    except Exception:
        return None, {"ok": False, "error": f"Invalid file_path: {file_path}"}

    root = repo_root().resolve()
    allowed_dir = (root / "deploy" / "k8s").resolve()

    try:
        p.relative_to(allowed_dir)
    except Exception:
        return None, {"ok": False, "error": "file_path must be under deploy/k8s", "allowed_dir": str(allowed_dir)}

    if not p.is_file():
        return None, {"ok": False, "error": f"Manifest file not found: {str(p)}"}

    return p, None


def kubectl_or_error() -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return the kubectl binary path or an error dict."""
    kubectl = shutil.which("kubectl")
    if not kubectl:
        return None, {"ok": False, "error": "kubectl not found in PATH", "hint": "Install kubectl or ensure it is on PATH."}
    return kubectl, None
