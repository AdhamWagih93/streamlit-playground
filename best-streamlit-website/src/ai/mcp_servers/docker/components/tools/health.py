"""Docker health check tool implementation."""
from __future__ import annotations

from typing import Any, Dict

from ...client import (
    client_or_error,
    docker_cli_or_error,
    run_docker_cli,
    run_docker_cli_json_lines,
    run_docker_cli_json_object,
)


def health_check() -> Dict[str, Any]:
    """Ping the Docker daemon and return its version."""

    # Fast-fail: the Docker SDK can hang on Windows named pipe issues.
    cli, err = client_or_error(timeout_seconds=3)
    if not err and cli is not None:
        try:
            ok = bool(cli.ping())
            ver = cli.version() if ok else None
            base_url = None
            try:
                base_url = getattr(getattr(cli, "api", None), "base_url", None)
            except Exception:
                base_url = None
            return {"ok": ok, "version": ver, "base_url": base_url, "backend": "docker-sdk"}
        except Exception as exc:
            err = {"ok": False, "error": str(exc)}

    # Fallback to docker CLI to match the user's working Docker context.
    docker_bin, derr = docker_cli_or_error()
    context = None
    if not derr:
        ctx_out, _ = run_docker_cli(["context", "show"], timeout=3)
        context = (ctx_out or "").strip() or None

    probe: Dict[str, Any] = {"context": context, "docker_bin": docker_bin}

    rows, cerr = run_docker_cli_json_lines(["ps", "-a", "--format", "{{json .}}"], timeout=6)
    if not cerr:
        probe["probe"] = "docker ps"
        probe["container_count"] = len(rows or [])
        return {"ok": True, "backend": "docker-cli", "probe": probe}

    rows, cerr2 = run_docker_cli_json_lines(["images", "-a", "--format", "{{json .}}"], timeout=6)
    if not cerr2:
        probe["probe"] = "docker images"
        probe["image_count"] = len(rows or [])
        return {"ok": True, "backend": "docker-cli", "probe": probe}

    obj, cerr3 = run_docker_cli_json_object(["version", "--format", "{{json .}}"], timeout=6)
    if not cerr3:
        probe["probe"] = "docker version"
        return {"ok": True, "backend": "docker-cli", "version": obj, "probe": probe}

    return {
        "ok": False,
        "error": "docker_unreachable",
        "sdk_error": err,
        "cli_error": {"ps": cerr, "images": cerr2, "version": cerr3, "docker_bin": docker_bin, "context": context},
    }
