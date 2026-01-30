"""Git MCP tools implementation."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .client_factory import client_from_env


def git_health_check() -> Dict[str, Any]:
    """Check git availability and version."""
    c = client_from_env()
    return c.version()


def git_is_repo(path: Optional[str] = None) -> Dict[str, Any]:
    """Check if a path is a git repository."""
    c = client_from_env()
    return c.is_repo(path)


def git_status(path: Optional[str] = None) -> Dict[str, Any]:
    """Get repository status (modified, staged, untracked files)."""
    c = client_from_env()
    return c.status(path)


def git_log(
    path: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Get commit history."""
    c = client_from_env()
    return c.log(path=path, limit=limit)


def git_branches(
    path: Optional[str] = None,
    all_branches: bool = False,
    remote: bool = False,
) -> Dict[str, Any]:
    """List branches."""
    c = client_from_env()
    return c.branch(path=path, all_branches=all_branches, remote=remote)


def git_current_branch(path: Optional[str] = None) -> Dict[str, Any]:
    """Get the current branch name."""
    c = client_from_env()
    return c.current_branch(path)


def git_diff(
    path: Optional[str] = None,
    staged: bool = False,
    file_path: Optional[str] = None,
    stat: bool = False,
) -> Dict[str, Any]:
    """Get diff of changes."""
    c = client_from_env()
    return c.diff(path=path, staged=staged, file_path=file_path, stat=stat)


def git_show(
    commit: str = "HEAD",
    path: Optional[str] = None,
    stat: bool = False,
) -> Dict[str, Any]:
    """Show commit details."""
    c = client_from_env()
    return c.show(commit=commit, path=path, stat=stat)


def git_remotes(path: Optional[str] = None) -> Dict[str, Any]:
    """List configured remotes."""
    c = client_from_env()
    return c.remote(path)


def git_tags(
    path: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """List tags."""
    c = client_from_env()
    return c.tags(path=path, limit=limit)


def git_blame(
    file_path: str,
    path: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> Dict[str, Any]:
    """Get blame information for a file."""
    c = client_from_env()
    line_range: Optional[Tuple[int, int]] = None
    if start_line is not None and end_line is not None:
        line_range = (start_line, end_line)
    return c.blame(file_path=file_path, path=path, line_range=line_range)


def git_stash_list(path: Optional[str] = None) -> Dict[str, Any]:
    """List stashes."""
    c = client_from_env()
    return c.stash_list(path)


def git_config_get(
    key: str,
    path: Optional[str] = None,
    global_config: bool = False,
) -> Dict[str, Any]:
    """Get a git configuration value."""
    c = client_from_env()
    return c.config_get(key=key, path=path, global_config=global_config)


def git_fetch(
    path: Optional[str] = None,
    remote: str = "origin",
    prune: bool = False,
) -> Dict[str, Any]:
    """Fetch from remote."""
    c = client_from_env()
    return c.fetch(path=path, remote=remote, prune=prune)


def git_pull(
    path: Optional[str] = None,
    remote: str = "origin",
    branch: Optional[str] = None,
) -> Dict[str, Any]:
    """Pull from remote."""
    c = client_from_env()
    return c.pull(path=path, remote=remote, branch=branch)


def git_checkout(
    ref: str,
    path: Optional[str] = None,
    create: bool = False,
) -> Dict[str, Any]:
    """Checkout a branch or commit."""
    c = client_from_env()
    return c.checkout(ref=ref, path=path, create=create)


def git_clone(
    url: str,
    dest: str,
    branch: Optional[str] = None,
    depth: Optional[int] = None,
) -> Dict[str, Any]:
    """Clone a repository."""
    c = client_from_env()
    return c.clone(url=url, dest=dest, branch=branch, depth=depth)
