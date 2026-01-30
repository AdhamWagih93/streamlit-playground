"""Docker client factory and utilities.

This module provides the Docker client factory and utility functions
used by the Docker MCP server tools.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from .config import DockerMCPServerConfig


def _subprocess_creationflags() -> int:
    """Avoid flashing a console window on Windows."""
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return int(subprocess.CREATE_NO_WINDOW)
    return 0


def docker_cli_or_error() -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return the docker CLI path or an error dict."""
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return None, {"ok": False, "error": "docker CLI not found in PATH"}
    return docker_bin, None


def run_docker_cli(args: List[str], timeout: int = 30) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Run a docker CLI command and return stdout or error."""
    docker_bin, err = docker_cli_or_error()
    if err:
        return None, err

    cmd = [docker_bin] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(timeout),
            creationflags=_subprocess_creationflags(),
        )
    except subprocess.TimeoutExpired:
        return None, {"ok": False, "error": "docker_cli_timeout", "command": cmd}

    if proc.returncode != 0:
        return None, {
            "ok": False,
            "error": "docker_cli_failed",
            "command": cmd,
            "exit_code": int(proc.returncode),
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }

    return (proc.stdout or ""), None


def run_docker_cli_json_lines(args: List[str], timeout: int = 30) -> tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
    """Run a docker CLI command expecting JSON lines output."""
    docker_bin, err = docker_cli_or_error()
    if err:
        return None, err

    cmd = [docker_bin] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(timeout),
            creationflags=_subprocess_creationflags(),
        )
    except subprocess.TimeoutExpired:
        return None, {"ok": False, "error": "docker_cli_timeout", "command": cmd}

    if proc.returncode != 0:
        return None, {
            "ok": False,
            "error": "docker_cli_failed",
            "command": cmd,
            "exit_code": int(proc.returncode),
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }

    rows: List[Dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except Exception:
            continue

    return rows, None


def run_docker_cli_json_object(args: List[str], timeout: int = 30) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Run a docker CLI command expecting a single JSON object output."""
    docker_bin, err = docker_cli_or_error()
    if err:
        return None, err

    cmd = [docker_bin] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(timeout),
            creationflags=_subprocess_creationflags(),
        )
    except subprocess.TimeoutExpired:
        return None, {"ok": False, "error": "docker_cli_timeout", "command": cmd}

    if proc.returncode != 0:
        return None, {
            "ok": False,
            "error": "docker_cli_failed",
            "command": cmd,
            "exit_code": int(proc.returncode),
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }

    txt = (proc.stdout or "").strip()
    if not txt:
        return None, {"ok": False, "error": "docker_cli_empty_output", "command": cmd}
    try:
        obj = json.loads(txt)
    except Exception as exc:
        return None, {"ok": False, "error": f"docker_cli_invalid_json: {exc}", "output": txt[:4000], "command": cmd}
    if not isinstance(obj, dict):
        return None, {"ok": False, "error": "docker_cli_non_object_json", "output": txt[:4000], "command": cmd}
    return obj, None


def client_or_error(timeout_seconds: Optional[int] = None) -> tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Return a docker client or an MCP-style error dict.

    This MCP server intentionally does not require the Docker CLI, but it does
    require the Python Docker SDK (package: 'docker').
    """
    try:
        import docker
    except ModuleNotFoundError:
        return (
            None,
            {
                "ok": False,
                "error": "Python package 'docker' is not installed.",
                "hint": "Install dependencies: python -m pip install -r requirements.txt (or: python -m pip install docker)",
            },
        )

    cfg = DockerMCPServerConfig.from_env()
    timeout_s = int(timeout_seconds) if timeout_seconds is not None else int(cfg.docker_timeout_seconds)
    try:
        return docker.from_env(timeout=timeout_s), None
    except Exception as exc:
        # On Windows, docker-py typically talks to Docker Desktop via a named pipe.
        if os.name == "nt" and not cfg.docker_host:
            try:
                return (
                    docker.DockerClient(
                        base_url="npipe:////./pipe/docker_engine",
                        timeout=timeout_s,
                    ),
                    None,
                )
            except Exception:
                pass
        return None, {"ok": False, "error": str(exc)}


def container_to_row(c) -> Dict[str, Any]:
    """Convert a container object to a dict."""
    attrs = getattr(c, "attrs", {}) or {}
    image = None
    try:
        image = getattr(getattr(c, "image", None), "tags", None)
    except Exception:
        image = None

    return {
        "id": getattr(c, "id", None),
        "name": getattr(c, "name", None),
        "status": getattr(c, "status", None),
        "image": image,
        "created": attrs.get("Created"),
        "labels": attrs.get("Config", {}).get("Labels") if isinstance(attrs.get("Config"), dict) else None,
    }


def image_to_row(img) -> Dict[str, Any]:
    """Convert an image object to a dict."""
    attrs = getattr(img, "attrs", {}) or {}
    return {
        "id": getattr(img, "id", None),
        "tags": getattr(img, "tags", None),
        "created": attrs.get("Created"),
        "size": attrs.get("Size"),
        "repo_digests": attrs.get("RepoDigests"),
    }
