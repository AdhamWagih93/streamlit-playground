from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from .config import LocalMCPServerConfig
from ..cache import configure_mcp_cache

from .prompts import register_prompts


mcp = FastMCP("local-mcp")
configure_mcp_cache(mcp, server_name="local")

# Prompts
register_prompts(mcp)


def _get_root() -> Path:
    cfg = LocalMCPServerConfig.from_env()
    root = Path(cfg.root_path).expanduser()
    try:
        return root.resolve()
    except Exception:
        return root.absolute()


def _require_write_enabled() -> Optional[Dict[str, Any]]:
    cfg = LocalMCPServerConfig.from_env()
    if not cfg.allow_write:
        return {"ok": False, "error": "write operations are disabled (LOCAL_MCP_ALLOW_WRITE=false)"}
    return None


def _resolve_path(rel_path: str) -> Path:
    root = _get_root()
    raw = (rel_path or "").strip()
    if raw in {"", "."}:
        return root

    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError("Absolute paths are not allowed. Provide a path relative to the root.")

    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except Exception as exc:
        raise ValueError("Path escapes configured root") from exc
    return resolved


def _read_text_sample(path: Path, *, max_bytes: int, encoding: str) -> Dict[str, Any]:
    with path.open("rb") as fh:
        raw = fh.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode(encoding, errors="replace")
    return {"text": text, "truncated": truncated, "bytes_read": min(len(raw), max_bytes)}


def _owner_info(stat) -> Dict[str, Any]:
    uid = getattr(stat, "st_uid", None)
    gid = getattr(stat, "st_gid", None)
    owner = None
    group = None

    if uid is not None:
        try:
            import pwd  # type: ignore

            owner = pwd.getpwuid(uid).pw_name
        except Exception:
            owner = None

    if gid is not None:
        try:
            import grp  # type: ignore

            group = grp.getgrgid(gid).gr_name
        except Exception:
            group = None

    return {
        "uid": uid,
        "gid": gid,
        "owner": owner,
        "group": group,
    }


def _path_info(path: Path, *, include_owner: bool, include_size: bool) -> Dict[str, Any]:
    stat = path.stat()
    info: Dict[str, Any] = {
        "name": path.name,
        "path": str(path.relative_to(_get_root()).as_posix()),
        "is_dir": path.is_dir(),
        "modified": stat.st_mtime,
    }
    if include_size and path.is_file():
        info["size"] = int(stat.st_size)
    if include_owner:
        info.update(_owner_info(stat))
    return info


@mcp.tool
def local_health_check() -> Dict[str, Any]:
    root = _get_root()
    cfg = LocalMCPServerConfig.from_env()
    return {
        "ok": True,
        "root": str(root),
        "allow_write": cfg.allow_write,
        "exists": root.exists(),
        "is_dir": root.is_dir(),
    }


@mcp.tool
def local_list_dir(path: str = "") -> Dict[str, Any]:
    """List files/directories under the given relative path."""
    try:
        target = _resolve_path(path)
        if not target.exists():
            return {"ok": False, "error": "path not found", "path": str(target)}
        if not target.is_dir():
            return {"ok": False, "error": "path is not a directory", "path": str(target)}

        entries: List[Dict[str, Any]] = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                entries.append(_path_info(item, include_owner=False, include_size=True))
            except Exception:
                entries.append({
                    "name": item.name,
                    "path": str(item.relative_to(_get_root()).as_posix()),
                    "is_dir": item.is_dir(),
                })
        return {"ok": True, "path": str(target), "entries": entries}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool
def local_stat(path: str) -> Dict[str, Any]:
    """Return stat/ownership metadata for a path relative to root."""
    try:
        target = _resolve_path(path)
        if not target.exists():
            return {"ok": False, "error": "path not found", "path": str(target)}
        info = _path_info(target, include_owner=True, include_size=True)
        info["ok"] = True
        return info
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool
def local_tree(
    path: str = "",
    max_depth: int = 3,
    max_entries: int = 500,
    include_hidden: bool = False,
    include_sizes: bool = True,
    include_owners: bool = False,
) -> Dict[str, Any]:
    """Return a tree listing with optional sizes and ownerships."""
    try:
        root = _resolve_path(path)
        if not root.exists():
            return {"ok": False, "error": "path not found", "path": str(root)}

        base_depth = len(root.parts)
        entries: List[Dict[str, Any]] = []

        for dirpath, dirnames, filenames in os.walk(root):
            dir_path = Path(dirpath)
            depth = len(dir_path.parts) - base_depth
            if depth > max_depth:
                dirnames[:] = []
                continue

            if not include_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                filenames = [f for f in filenames if not f.startswith(".")]

            try:
                entries.append(_path_info(dir_path, include_owner=include_owners, include_size=False))
                entries[-1]["depth"] = depth
            except Exception:
                entries.append({
                    "name": dir_path.name,
                    "path": str(dir_path.relative_to(_get_root()).as_posix()),
                    "is_dir": True,
                    "depth": depth,
                })

            for fname in filenames:
                fpath = dir_path / fname
                try:
                    info = _path_info(fpath, include_owner=include_owners, include_size=include_sizes)
                    info["depth"] = depth + 1
                    entries.append(info)
                except Exception:
                    entries.append({
                        "name": fname,
                        "path": str(fpath.relative_to(_get_root()).as_posix()),
                        "is_dir": False,
                        "depth": depth + 1,
                    })

                if len(entries) >= max_entries:
                    return {"ok": True, "path": str(root), "truncated": True, "entries": entries}

        return {"ok": True, "path": str(root), "truncated": False, "entries": entries}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool
def local_read_file(
    path: str,
    encoding: str = "utf-8",
    max_bytes: int = 200_000,
) -> Dict[str, Any]:
    """Read a text file relative to the configured root."""
    try:
        target = _resolve_path(path)
        if not target.exists():
            return {"ok": False, "error": "path not found", "path": str(target)}
        if target.is_dir():
            return {"ok": False, "error": "path is a directory", "path": str(target)}

        data = _read_text_sample(target, max_bytes=max_bytes, encoding=encoding)
        return {
            "ok": True,
            "path": str(target),
            "text": data["text"],
            "truncated": data["truncated"],
            "bytes_read": data["bytes_read"],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool
def local_write_file(
    path: str,
    content: str,
    mode: str = "overwrite",
    encoding: str = "utf-8",
    create_dirs: bool = True,
) -> Dict[str, Any]:
    """Write a text file relative to the configured root."""
    err = _require_write_enabled()
    if err:
        return err

    try:
        target = _resolve_path(path)
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)

        write_mode = "a" if str(mode).lower().strip() == "append" else "w"
        with target.open(write_mode, encoding=encoding) as fh:
            fh.write(content or "")
        return {"ok": True, "path": str(target), "mode": write_mode}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool
def local_search_filenames(
    query: str,
    path: str = "",
    max_results: int = 200,
    case_sensitive: bool = False,
) -> Dict[str, Any]:
    """Search file/dir names under the root for a substring match."""
    try:
        root = _resolve_path(path)
        if not root.exists():
            return {"ok": False, "error": "path not found", "path": str(root)}

        q = query or ""
        if not q:
            return {"ok": False, "error": "query is required"}

        if not case_sensitive:
            q = q.lower()

        results: List[Dict[str, Any]] = []
        for item in root.rglob("*"):
            name = item.name
            hay = name if case_sensitive else name.lower()
            if q in hay:
                results.append({
                    "name": name,
                    "path": str(item.relative_to(_get_root()).as_posix()),
                    "is_dir": item.is_dir(),
                })
                if len(results) >= max_results:
                    break

        return {"ok": True, "count": len(results), "results": results}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool
def local_search_contents(
    query: str,
    path: str = "",
    max_results: int = 50,
    case_sensitive: bool = False,
    max_bytes: int = 200_000,
    file_glob: str = "**/*",
) -> Dict[str, Any]:
    """Search file contents under the root for a substring match."""
    try:
        root = _resolve_path(path)
        if not root.exists():
            return {"ok": False, "error": "path not found", "path": str(root)}

        q = query or ""
        if not q:
            return {"ok": False, "error": "query is required"}

        needle = q if case_sensitive else q.lower()
        results: List[Dict[str, Any]] = []

        for item in root.glob(file_glob):
            if item.is_dir():
                continue

            try:
                sample = _read_text_sample(item, max_bytes=max_bytes, encoding="utf-8")
            except Exception:
                continue

            text = sample["text"]
            hay = text if case_sensitive else text.lower()
            if needle not in hay:
                continue

            for idx, line in enumerate(text.splitlines(), start=1):
                hay_line = line if case_sensitive else line.lower()
                if needle in hay_line:
                    results.append({
                        "path": str(item.relative_to(_get_root()).as_posix()),
                        "line": idx,
                        "text": line[:300],
                    })
                    if len(results) >= max_results:
                        break

            if len(results) >= max_results:
                break

        return {"ok": True, "count": len(results), "results": results}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def run_stdio() -> None:
    """Run the Local MCP server over HTTP."""
    cfg = LocalMCPServerConfig.from_env()

    host = os.environ.get("MCP_HOST") or cfg.mcp_host
    port_raw = os.environ.get("MCP_PORT")
    try:
        port = int(port_raw) if port_raw else int(cfg.mcp_port)
    except Exception:
        port = int(cfg.mcp_port)

    try:
        mcp.run(transport="http", host=host, port=port)
    except TypeError:
        mcp.run(transport="http")


if __name__ == "__main__":
    run_stdio()
