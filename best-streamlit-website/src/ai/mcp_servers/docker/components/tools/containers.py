"""Docker container management tool implementations."""
from __future__ import annotations

from typing import Any, Dict, List

from ...client import (
    client_or_error,
    container_to_row,
    run_docker_cli_json_lines,
)


def list_containers(all: bool = False) -> Dict[str, Any]:
    """List containers."""

    cli, err = client_or_error(timeout_seconds=8)
    if not err and cli is not None:
        try:
            containers = cli.containers.list(all=bool(all))
            return {"ok": True, "containers": [container_to_row(c) for c in containers], "backend": "docker-sdk"}
        except Exception as exc:
            err = {"ok": False, "error": str(exc)}

    args = ["ps"]
    if bool(all):
        args.append("-a")
    args += ["--format", "{{json .}}"]

    rows, cerr = run_docker_cli_json_lines(args, timeout=12)
    if cerr:
        return {"ok": False, "error": "docker_list_containers_failed", "sdk_error": err, "cli_error": cerr}

    out: List[Dict[str, Any]] = []
    for r in rows or []:
        out.append(
            {
                "id": r.get("ID"),
                "name": r.get("Names"),
                "status": r.get("Status"),
                "image": [r.get("Image")] if r.get("Image") else None,
                "created": r.get("CreatedAt"),
                "labels": r.get("Labels"),
            }
        )

    return {"ok": True, "containers": out, "backend": "docker-cli"}


def start_container(container_id: str) -> Dict[str, Any]:
    """Start a container."""
    cli, err = client_or_error()
    if err:
        return err
    c = cli.containers.get(container_id)
    c.start()
    return {"ok": True}


def stop_container(container_id: str, timeout: int = 10) -> Dict[str, Any]:
    """Stop a container."""
    cli, err = client_or_error()
    if err:
        return err
    c = cli.containers.get(container_id)
    c.stop(timeout=int(timeout))
    return {"ok": True}


def restart_container(container_id: str, timeout: int = 10) -> Dict[str, Any]:
    """Restart a container."""
    cli, err = client_or_error()
    if err:
        return err
    c = cli.containers.get(container_id)
    c.restart(timeout=int(timeout))
    return {"ok": True}


def remove_container(container_id: str, force: bool = False, remove_volumes: bool = False) -> Dict[str, Any]:
    """Remove a container."""
    cli, err = client_or_error()
    if err:
        return err
    c = cli.containers.get(container_id)
    c.remove(force=bool(force), v=bool(remove_volumes))
    return {"ok": True}


def container_logs(container_id: str, tail: int = 200, timestamps: bool = True) -> Dict[str, Any]:
    """Get container logs."""
    cli, err = client_or_error()
    if err:
        return err
    c = cli.containers.get(container_id)
    data = c.logs(tail=int(tail), timestamps=bool(timestamps))
    try:
        text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data)
    except Exception:
        text = str(data)
    return {"ok": True, "text": text}
