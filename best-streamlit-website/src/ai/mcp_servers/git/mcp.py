from __future__ import annotations

import inspect
import os
from typing import Any, Dict, Optional

from fastmcp import FastMCP

from .config import GitMCPServerConfig
from .utils.client import GitClient


mcp = FastMCP("git-mcp")

_CLIENT: Optional[GitClient] = None


def _client_from_env() -> GitClient:
    """Get or create a GitClient from environment configuration."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg = GitMCPServerConfig.from_env()
    _CLIENT = GitClient(
        repo_path=cfg.repo_path,
        timeout_seconds=cfg.timeout_seconds,
    )
    return _CLIENT


@mcp.tool
def git_health_check() -> Dict[str, Any]:
    """Check git availability and version."""
    c = _client_from_env()
    return c.version()


@mcp.tool
def git_is_repo(path: Optional[str] = None) -> Dict[str, Any]:
    """Check if a path is a git repository.

    Args:
        path: Path to check (uses configured default if not provided)
    """
    c = _client_from_env()
    return c.is_repo(path)


@mcp.tool
def git_status(path: Optional[str] = None) -> Dict[str, Any]:
    """Get repository status (modified, staged, untracked files).

    Args:
        path: Repository path (uses configured default if not provided)
    """
    c = _client_from_env()
    return c.status(path)


@mcp.tool
def git_log(
    path: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Get commit history.

    Args:
        path: Repository path (uses configured default if not provided)
        limit: Maximum number of commits to return (default: 20)
    """
    c = _client_from_env()
    return c.log(path=path, limit=limit)


@mcp.tool
def git_branches(
    path: Optional[str] = None,
    all_branches: bool = False,
    remote: bool = False,
) -> Dict[str, Any]:
    """List branches.

    Args:
        path: Repository path (uses configured default if not provided)
        all_branches: Include remote-tracking branches
        remote: Only show remote branches
    """
    c = _client_from_env()
    return c.branch(path=path, all_branches=all_branches, remote=remote)


@mcp.tool
def git_current_branch(path: Optional[str] = None) -> Dict[str, Any]:
    """Get the current branch name.

    Args:
        path: Repository path (uses configured default if not provided)
    """
    c = _client_from_env()
    return c.current_branch(path)


@mcp.tool
def git_diff(
    path: Optional[str] = None,
    staged: bool = False,
    file_path: Optional[str] = None,
    stat: bool = False,
) -> Dict[str, Any]:
    """Get diff of changes.

    Args:
        path: Repository path (uses configured default if not provided)
        staged: Show staged changes only
        file_path: Limit diff to specific file
        stat: Show diffstat instead of full diff
    """
    c = _client_from_env()
    return c.diff(path=path, staged=staged, file_path=file_path, stat=stat)


@mcp.tool
def git_show(
    commit: str = "HEAD",
    path: Optional[str] = None,
    stat: bool = False,
) -> Dict[str, Any]:
    """Show commit details.

    Args:
        commit: Commit hash or reference (default: HEAD)
        path: Repository path (uses configured default if not provided)
        stat: Show diffstat only
    """
    c = _client_from_env()
    return c.show(commit=commit, path=path, stat=stat)


@mcp.tool
def git_remotes(path: Optional[str] = None) -> Dict[str, Any]:
    """List configured remotes.

    Args:
        path: Repository path (uses configured default if not provided)
    """
    c = _client_from_env()
    return c.remote(path)


@mcp.tool
def git_tags(
    path: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """List tags.

    Args:
        path: Repository path (uses configured default if not provided)
        limit: Maximum number of tags to return (default: 50)
    """
    c = _client_from_env()
    return c.tags(path=path, limit=limit)


@mcp.tool
def git_blame(
    file_path: str,
    path: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> Dict[str, Any]:
    """Get blame information for a file.

    Args:
        file_path: Path to file within repository
        path: Repository path (uses configured default if not provided)
        start_line: Starting line number (optional)
        end_line: Ending line number (optional)
    """
    c = _client_from_env()
    line_range = None
    if start_line is not None and end_line is not None:
        line_range = (start_line, end_line)
    return c.blame(file_path=file_path, path=path, line_range=line_range)


@mcp.tool
def git_stash_list(path: Optional[str] = None) -> Dict[str, Any]:
    """List stashes.

    Args:
        path: Repository path (uses configured default if not provided)
    """
    c = _client_from_env()
    return c.stash_list(path)


@mcp.tool
def git_config_get(
    key: str,
    path: Optional[str] = None,
    global_config: bool = False,
) -> Dict[str, Any]:
    """Get a git configuration value.

    Args:
        key: Configuration key (e.g., user.name, user.email)
        path: Repository path (uses configured default if not provided)
        global_config: Read from global config instead of local
    """
    c = _client_from_env()
    return c.config_get(key=key, path=path, global_config=global_config)


@mcp.tool
def git_fetch(
    path: Optional[str] = None,
    remote: str = "origin",
    prune: bool = False,
) -> Dict[str, Any]:
    """Fetch from remote.

    Args:
        path: Repository path (uses configured default if not provided)
        remote: Remote name (default: origin)
        prune: Remove remote-tracking references that no longer exist
    """
    c = _client_from_env()
    return c.fetch(path=path, remote=remote, prune=prune)


@mcp.tool
def git_pull(
    path: Optional[str] = None,
    remote: str = "origin",
    branch: Optional[str] = None,
) -> Dict[str, Any]:
    """Pull from remote.

    Args:
        path: Repository path (uses configured default if not provided)
        remote: Remote name (default: origin)
        branch: Branch to pull (default: current branch)
    """
    c = _client_from_env()
    return c.pull(path=path, remote=remote, branch=branch)


@mcp.tool
def git_checkout(
    ref: str,
    path: Optional[str] = None,
    create: bool = False,
) -> Dict[str, Any]:
    """Checkout a branch or commit.

    Args:
        ref: Branch name, tag, or commit hash
        path: Repository path (uses configured default if not provided)
        create: Create new branch if it doesn't exist
    """
    c = _client_from_env()
    return c.checkout(ref=ref, path=path, create=create)


@mcp.tool
def git_clone(
    url: str,
    dest: str,
    branch: Optional[str] = None,
    depth: Optional[int] = None,
) -> Dict[str, Any]:
    """Clone a repository.

    Args:
        url: Repository URL
        dest: Destination directory
        branch: Specific branch to clone
        depth: Create a shallow clone with specified depth
    """
    c = _client_from_env()
    return c.clone(url=url, dest=dest, branch=branch, depth=depth)


def run_stdio() -> None:
    """Run the Git MCP server over HTTP.

    The function name is kept for backwards compatibility with existing
    entrypoints, but the server no longer supports stdio transport.
    """

    cfg = GitMCPServerConfig.from_env()

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
