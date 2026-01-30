"""Docker image management tool implementations."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ...client import (
    client_or_error,
    image_to_row,
    run_docker_cli_json_lines,
)


def list_images() -> Dict[str, Any]:
    """List images."""

    cli, err = client_or_error(timeout_seconds=8)
    if not err and cli is not None:
        try:
            images = cli.images.list()
            return {"ok": True, "images": [image_to_row(i) for i in images], "backend": "docker-sdk"}
        except Exception as exc:
            err = {"ok": False, "error": str(exc)}

    rows, cerr = run_docker_cli_json_lines(["images", "-a", "--format", "{{json .}}"], timeout=12)
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


def pull_image(ref: str) -> Dict[str, Any]:
    """Pull an image from a registry."""
    cli, err = client_or_error()
    if err:
        return err
    img = cli.images.pull(ref)
    return {"ok": True, "image": image_to_row(img)}


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
    cli, err = client_or_error()
    if err:
        return err

    reg = (registry or "").strip()
    if not reg:
        return {"ok": False, "error": "registry is required"}
    if not (username or "").strip() or not (password or "").strip():
        return {"ok": False, "error": "username/password are required"}

    try:
        res = cli.login(username=username, password=password, registry=reg)
        return {"ok": True, "result": res}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


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
    cli, err = client_or_error()
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
        image, logs = cli.images.build(
            path=str(ctx_path),
            dockerfile=df_path,
            tag=str(tag),
            buildargs=dict(build_args or {}),
            target=str(target) if target else None,
            rm=True,
            pull=False,
            nocache=bool(nocache),
        )

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

        return {"ok": True, "image": image_to_row(image), "log_tail": tail}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def tag_image(source: str, target: str) -> Dict[str, Any]:
    """Add an additional tag to an existing local image."""
    cli, err = client_or_error()
    if err:
        return err

    if not (source or "").strip() or not (target or "").strip():
        return {"ok": False, "error": "source and target are required"}

    try:
        img = cli.images.get(source)
        if ":" in target:
            repo, tag = target.rsplit(":", 1)
        else:
            repo, tag = target, "latest"
        ok = bool(img.tag(repository=repo, tag=tag))
        return {"ok": ok, "image": image_to_row(img), "target": target}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def push_image(ref: str) -> Dict[str, Any]:
    """Push an image to its registry (streaming push output)."""
    cli, err = client_or_error()
    if err:
        return err

    if not (ref or "").strip():
        return {"ok": False, "error": "ref is required"}

    try:
        if ":" in ref:
            repository, tag = ref.rsplit(":", 1)
        else:
            repository, tag = ref, "latest"

        stream = cli.images.push(repository=repository, tag=tag, stream=True, decode=True)
        tail: List[Any] = []
        for entry in stream or []:
            tail.append(entry)
            if len(tail) > 60:
                tail = tail[-60:]

        errors = []
        for e in tail:
            if isinstance(e, dict) and (e.get("error") or e.get("errorDetail")):
                errors.append(e.get("error") or e.get("errorDetail"))

        if errors:
            return {"ok": False, "error": "push_failed", "details": errors, "output_tail": tail}
        return {"ok": True, "output_tail": tail}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def remove_image(image_id: str, force: bool = False) -> Dict[str, Any]:
    """Remove an image."""
    cli, err = client_or_error()
    if err:
        return err
    cli.images.remove(image=image_id, force=bool(force))
    return {"ok": True}
