"""Kubectl command tools."""
from __future__ import annotations

import subprocess
from typing import Any, Dict, Optional

from .client_factory import clients_from_env, kubectl_or_error, validate_manifest_path
from ...utils.terminal import kubectl_like as kubectl_like_cmd


def kubectl_apply(file_path: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    """Apply a local manifest under deploy/k8s using kubectl.

    This is intended for Streamlit's Setup 'Using Kubernetes' flow.
    """
    p, err = validate_manifest_path(file_path)
    if err:
        return err

    kubectl, kerr = kubectl_or_error()
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


def kubectl_delete(file_path: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    """Delete resources in a local manifest under deploy/k8s using kubectl."""
    p, err = validate_manifest_path(file_path)
    if err:
        return err

    kubectl, kerr = kubectl_or_error()
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


def kubectl_like(command: str) -> Dict[str, Any]:
    """Execute a limited, safe subset of kubectl-style commands."""
    c = clients_from_env()
    return kubectl_like_cmd(c, command)
