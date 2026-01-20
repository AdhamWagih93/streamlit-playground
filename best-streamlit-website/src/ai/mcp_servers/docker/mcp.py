from __future__ import annotations

import inspect
import os
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from .config import DockerMCPServerConfig


mcp = FastMCP("docker-mcp")


def _client_or_error() -> tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Return a docker client or an MCP-style error dict.

    This MCP server intentionally does not require the Docker CLI, but it does
    require the Python Docker SDK (package: 'docker').
    """

    try:
        import docker  # type: ignore
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
    try:
        return docker.from_env(timeout=int(cfg.docker_timeout_seconds)), None
    except Exception as exc:  # noqa: BLE001
        return None, {"ok": False, "error": str(exc)}


def _container_to_row(c) -> Dict[str, Any]:
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


def _image_to_row(img) -> Dict[str, Any]:
    attrs = getattr(img, "attrs", {}) or {}
    return {
        "id": getattr(img, "id", None),
        "tags": getattr(img, "tags", None),
        "created": attrs.get("Created"),
        "size": attrs.get("Size"),
        "repo_digests": attrs.get("RepoDigests"),
    }


@mcp.tool
def health_check() -> Dict[str, Any]:
    """Ping the Docker daemon and return its version."""

    cli, err = _client_or_error()
    if err:
        return err

    try:
        ok = bool(cli.ping())  # type: ignore[union-attr]
        ver = cli.version() if ok else None  # type: ignore[union-attr]
        return {"ok": ok, "version": ver}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool
def list_containers(all: bool = False) -> Dict[str, Any]:
    """List containers."""

    cli, err = _client_or_error()
    if err:
        return err

    containers = cli.containers.list(all=bool(all))  # type: ignore[union-attr]
    return {"ok": True, "containers": [_container_to_row(c) for c in containers]}


@mcp.tool
def start_container(container_id: str) -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    c = cli.containers.get(container_id)  # type: ignore[union-attr]
    c.start()
    return {"ok": True}


@mcp.tool
def stop_container(container_id: str, timeout: int = 10) -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    c = cli.containers.get(container_id)  # type: ignore[union-attr]
    c.stop(timeout=int(timeout))
    return {"ok": True}


@mcp.tool
def restart_container(container_id: str, timeout: int = 10) -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    c = cli.containers.get(container_id)  # type: ignore[union-attr]
    c.restart(timeout=int(timeout))
    return {"ok": True}


@mcp.tool
def remove_container(container_id: str, force: bool = False, remove_volumes: bool = False) -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    c = cli.containers.get(container_id)  # type: ignore[union-attr]
    c.remove(force=bool(force), v=bool(remove_volumes))
    return {"ok": True}


@mcp.tool
def container_logs(container_id: str, tail: int = 200, timestamps: bool = True) -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    c = cli.containers.get(container_id)  # type: ignore[union-attr]
    data = c.logs(tail=int(tail), timestamps=bool(timestamps))
    try:
        text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data)
    except Exception:
        text = str(data)
    return {"ok": True, "text": text}


@mcp.tool
def list_images() -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    images = cli.images.list()  # type: ignore[union-attr]
    return {"ok": True, "images": [_image_to_row(i) for i in images]}


@mcp.tool
def pull_image(ref: str) -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    img = cli.images.pull(ref)  # type: ignore[union-attr]
    return {"ok": True, "image": _image_to_row(img)}


@mcp.tool
def remove_image(image_id: str, force: bool = False) -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    cli.images.remove(image=image_id, force=bool(force))  # type: ignore[union-attr]
    return {"ok": True}


@mcp.tool
def list_networks() -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    nets = cli.networks.list()  # type: ignore[union-attr]
    rows: List[Dict[str, Any]] = []
    for n in nets:
        attrs = getattr(n, "attrs", {}) or {}
        rows.append({"id": getattr(n, "id", None), "name": getattr(n, "name", None), "driver": attrs.get("Driver")})
    return {"ok": True, "networks": rows}


@mcp.tool
def list_volumes() -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    vols = cli.volumes.list()  # type: ignore[union-attr]
    rows: List[Dict[str, Any]] = []
    for v in vols or []:
        attrs = getattr(v, "attrs", {}) or {}
        rows.append({"name": attrs.get("Name"), "driver": attrs.get("Driver"), "mountpoint": attrs.get("Mountpoint")})
    return {"ok": True, "volumes": rows}


def run_stdio() -> None:
    cfg = DockerMCPServerConfig.from_env()
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
