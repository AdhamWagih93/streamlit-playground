from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from .config import DockerMCPServerConfig


mcp = FastMCP("docker-mcp")


def _subprocess_creationflags() -> int:
    # Avoid flashing a console window on Windows.
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return int(subprocess.CREATE_NO_WINDOW)
    return 0


def _docker_cli_or_error() -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return None, {"ok": False, "error": "docker CLI not found in PATH"}
    return docker_bin, None


def _run_docker_cli(args: List[str], timeout: int = 30) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    docker_bin, err = _docker_cli_or_error()
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


def _run_docker_cli_json_lines(args: List[str], timeout: int = 30) -> tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
    docker_bin, err = _docker_cli_or_error()
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
            # ignore non-json lines
            continue

    return rows, None


def _run_docker_cli_json_object(args: List[str], timeout: int = 30) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    docker_bin, err = _docker_cli_or_error()
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
    except Exception as exc:  # noqa: BLE001
        return None, {"ok": False, "error": f"docker_cli_invalid_json: {exc}", "output": txt[:4000], "command": cmd}
    if not isinstance(obj, dict):
        return None, {"ok": False, "error": "docker_cli_non_object_json", "output": txt[:4000], "command": cmd}
    return obj, None


def _client_or_error(timeout_seconds: Optional[int] = None) -> tuple[Optional[Any], Optional[Dict[str, Any]]]:
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
    timeout_s = int(timeout_seconds) if timeout_seconds is not None else int(cfg.docker_timeout_seconds)
    try:
        return docker.from_env(timeout=timeout_s), None
    except Exception as exc:  # noqa: BLE001
        # On Windows, docker-py typically talks to Docker Desktop via a named pipe.
        # If env vars are missing/misconfigured, try the default npipe endpoint.
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

    # Fast-fail: the Docker SDK can hang on Windows named pipe issues.
    cli, err = _client_or_error(timeout_seconds=3)
    if not err and cli is not None:
        try:
            ok = bool(cli.ping())  # type: ignore[union-attr]
            ver = cli.version() if ok else None  # type: ignore[union-attr]
            base_url = None
            try:
                base_url = getattr(getattr(cli, "api", None), "base_url", None)  # type: ignore[union-attr]
            except Exception:
                base_url = None
            return {"ok": ok, "version": ver, "base_url": base_url, "backend": "docker-sdk"}
        except Exception as exc:  # noqa: BLE001
            err = {"ok": False, "error": str(exc)}

    # Fallback to docker CLI to match the user's working Docker context.
    # Prefer fast probes first to keep the UI responsive.
    docker_bin, derr = _docker_cli_or_error()
    context = None
    if not derr:
        ctx_out, _ = _run_docker_cli(["context", "show"], timeout=3)
        context = (ctx_out or "").strip() or None

    # Probe the daemon using whichever command succeeds fastest.
    # If any of these succeed, Docker is reachable.
    probe: Dict[str, Any] = {"context": context, "docker_bin": docker_bin}

    rows, cerr = _run_docker_cli_json_lines(["ps", "-a", "--format", "{{json .}}"], timeout=6)
    if not cerr:
        probe["probe"] = "docker ps"
        probe["container_count"] = len(rows or [])
        return {"ok": True, "backend": "docker-cli", "probe": probe}

    rows, cerr2 = _run_docker_cli_json_lines(["images", "-a", "--format", "{{json .}}"], timeout=6)
    if not cerr2:
        probe["probe"] = "docker images"
        probe["image_count"] = len(rows or [])
        return {"ok": True, "backend": "docker-cli", "probe": probe}

    obj, cerr3 = _run_docker_cli_json_object(["version", "--format", "{{json .}}"], timeout=6)
    if not cerr3:
        probe["probe"] = "docker version"
        return {"ok": True, "backend": "docker-cli", "version": obj, "probe": probe}

    # None of the CLI probes worked.
    return {
        "ok": False,
        "error": "docker_unreachable",
        "sdk_error": err,
        "cli_error": {"ps": cerr, "images": cerr2, "version": cerr3, "docker_bin": docker_bin, "context": context},
    }


@mcp.tool
def list_containers(all: bool = False) -> Dict[str, Any]:
    """List containers."""

    cli, err = _client_or_error(timeout_seconds=8)
    if not err and cli is not None:
        try:
            containers = cli.containers.list(all=bool(all))  # type: ignore[union-attr]
            return {"ok": True, "containers": [_container_to_row(c) for c in containers], "backend": "docker-sdk"}
        except Exception as exc:  # noqa: BLE001
            err = {"ok": False, "error": str(exc)}

    args = ["ps"]
    if bool(all):
        args.append("-a")
    args += ["--format", "{{json .}}"]

    rows, cerr = _run_docker_cli_json_lines(args, timeout=12)
    if cerr:
        return {"ok": False, "error": "docker_list_containers_failed", "sdk_error": err, "cli_error": cerr}

    out: List[Dict[str, Any]] = []
    for r in rows or []:
        # docker ps JSON keys vary by version; these are the common ones.
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


@mcp.tool
def list_images() -> Dict[str, Any]:
    """List images."""

    cli, err = _client_or_error(timeout_seconds=8)
    if not err and cli is not None:
        try:
            images = cli.images.list()  # type: ignore[union-attr]
            return {"ok": True, "images": [_image_to_row(i) for i in images], "backend": "docker-sdk"}
        except Exception as exc:  # noqa: BLE001
            err = {"ok": False, "error": str(exc)}

    rows, cerr = _run_docker_cli_json_lines(["images", "-a", "--format", "{{json .}}"], timeout=12)
    if cerr:
        return {"ok": False, "error": "docker_list_images_failed", "sdk_error": err, "cli_error": cerr}

    imgs: List[Dict[str, Any]] = []
    for r in rows or []:
        repo = r.get("Repository")
        tag = r.get("Tag")
        tag_str = f"{repo}:{tag}" if repo and tag and tag != "<none>" else None
        imgs.append(
            {
                "id": r.get("ID"),
                "tags": [tag_str] if tag_str else [],
                "created": r.get("CreatedAt"),
                "size": r.get("Size"),
                "repo_digests": None,
            }
        )

    return {"ok": True, "images": imgs, "backend": "docker-cli"}


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
def pull_image(ref: str) -> Dict[str, Any]:
    cli, err = _client_or_error()
    if err:
        return err
    img = cli.images.pull(ref)  # type: ignore[union-attr]
    return {"ok": True, "image": _image_to_row(img)}


@mcp.tool
def docker_login(
    registry: str,
    username: str,
    password: str,
) -> Dict[str, Any]:
    """Log in to a Docker registry.

    Notes:
    - This is needed before pushing to private registries (e.g., Nexus Docker hosted).
    - Uses the Docker Engine API via docker-py (no docker CLI).
    """

    cli, err = _client_or_error()
    if err:
        return err

    reg = (registry or "").strip()
    if not reg:
        return {"ok": False, "error": "registry is required"}
    if not (username or "").strip() or not (password or "").strip():
        return {"ok": False, "error": "username/password are required"}

    try:
        res = cli.login(username=username, password=password, registry=reg)  # type: ignore[union-attr]
        return {"ok": True, "result": res}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool
def build_image(
    context_path: str,
    tag: str,
    dockerfile: str = "Dockerfile",
    build_args: Optional[Dict[str, str]] = None,
    target: Optional[str] = None,
    nocache: bool = False,
) -> Dict[str, Any]:
    """Build a Docker image from a local context.

    Parameters:
    - context_path: folder containing the Dockerfile
    - tag: resulting image tag
    - dockerfile: relative path under context_path
    - build_args: optional build-arg dict
    - target: optional multi-stage target
    """

    cli, err = _client_or_error()
    if err:
        return err

    ctx = (context_path or "").strip()
    if not ctx:
        return {"ok": False, "error": "context_path is required"}
    if not (tag or "").strip():
        return {"ok": False, "error": "tag is required"}

    ctx_path = Path(ctx)
    if not ctx_path.exists() or not ctx_path.is_dir():
        return {"ok": False, "error": f"context_path not found or not a directory: {ctx}"}

    df_path = (dockerfile or "Dockerfile").strip()
    if not df_path:
        df_path = "Dockerfile"

    try:
        image, logs = cli.images.build(  # type: ignore[union-attr]
            path=str(ctx_path),
            dockerfile=df_path,
            tag=str(tag),
            buildargs=dict(build_args or {}),
            target=str(target) if target else None,
            rm=True,
            pull=False,
            nocache=bool(nocache),
        )

        # Trim logs (can be huge)
        tail: List[str] = []
        try:
            for entry in (logs or [])[-80:]:
                if isinstance(entry, dict):
                    line = entry.get("stream") or entry.get("status") or entry.get("error")
                    if line:
                        tail.append(str(line).rstrip())
                else:
                    tail.append(str(entry).rstrip())
        except Exception:
            tail = []

        return {"ok": True, "image": _image_to_row(image), "log_tail": tail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool
def tag_image(source: str, target: str) -> Dict[str, Any]:
    """Add an additional tag to an existing local image."""

    cli, err = _client_or_error()
    if err:
        return err

    if not (source or "").strip() or not (target or "").strip():
        return {"ok": False, "error": "source and target are required"}

    try:
        img = cli.images.get(source)  # type: ignore[union-attr]
        # docker-py wants repository + tag
        if ":" in target:
            repo, tag = target.rsplit(":", 1)
        else:
            repo, tag = target, "latest"
        ok = bool(img.tag(repository=repo, tag=tag))
        return {"ok": ok, "image": _image_to_row(img), "target": target}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool
def push_image(ref: str) -> Dict[str, Any]:
    """Push an image to its registry (streaming push output)."""

    cli, err = _client_or_error()
    if err:
        return err

    if not (ref or "").strip():
        return {"ok": False, "error": "ref is required"}

    try:
        if ":" in ref:
            repository, tag = ref.rsplit(":", 1)
        else:
            repository, tag = ref, "latest"

        stream = cli.images.push(repository=repository, tag=tag, stream=True, decode=True)  # type: ignore[union-attr]
        tail: List[Any] = []
        for entry in stream or []:
            tail.append(entry)
            if len(tail) > 60:
                tail = tail[-60:]

        # Detect success/errors
        errors = []
        for e in tail:
            if isinstance(e, dict) and (e.get("error") or e.get("errorDetail")):
                errors.append(e.get("error") or e.get("errorDetail"))

        if errors:
            return {"ok": False, "error": "push_failed", "details": errors, "output_tail": tail}
        return {"ok": True, "output_tail": tail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


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
