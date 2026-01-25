from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _subprocess_creationflags() -> int:
    """Avoid flashing a console window on Windows."""
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return int(subprocess.CREATE_NO_WINDOW)
    return 0


@dataclass(frozen=True)
class GitResult:
    """Result of a git command execution."""

    ok: bool
    stdout: str
    stderr: str
    returncode: int
    command: List[str]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "command": self.command,
            "error": self.error,
        }


class GitClient:
    """Git CLI wrapper for MCP operations."""

    def __init__(
        self,
        repo_path: Optional[str] = None,
        timeout_seconds: int = 60,
    ):
        self.repo_path = repo_path
        self.timeout_seconds = timeout_seconds
        self._git_bin: Optional[str] = None

    def _find_git(self) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Locate the git binary."""
        if self._git_bin:
            return self._git_bin, None

        git_bin = shutil.which("git")
        if not git_bin:
            return None, {"ok": False, "error": "git CLI not found in PATH"}

        self._git_bin = git_bin
        return git_bin, None

    def _run(
        self,
        args: List[str],
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> GitResult:
        """Run a git command and return the result."""
        git_bin, err = self._find_git()
        if err:
            return GitResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                command=["git"] + args,
                error=err.get("error", "git not found"),
            )

        cmd = [git_bin] + list(args)
        work_dir = cwd or self.repo_path
        timeout_s = timeout if timeout is not None else self.timeout_seconds

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=work_dir,
                creationflags=_subprocess_creationflags(),
            )
        except subprocess.TimeoutExpired:
            return GitResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                command=cmd,
                error=f"Command timed out after {timeout_s}s",
            )
        except Exception as exc:
            return GitResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                command=cmd,
                error=str(exc),
            )

        return GitResult(
            ok=proc.returncode == 0,
            stdout=(proc.stdout or "").strip(),
            stderr=(proc.stderr or "").strip(),
            returncode=proc.returncode,
            command=cmd,
            error=None if proc.returncode == 0 else (proc.stderr or "").strip(),
        )

    def version(self) -> Dict[str, Any]:
        """Get git version."""
        result = self._run(["--version"])
        if result.ok:
            return {"ok": True, "version": result.stdout}
        return result.to_dict()

    def is_repo(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Check if path is a git repository."""
        work_dir = path or self.repo_path
        result = self._run(["rev-parse", "--is-inside-work-tree"], cwd=work_dir)
        return {
            "ok": result.ok,
            "is_repo": result.ok and result.stdout.lower() == "true",
            "path": work_dir,
        }

    def status(self, path: Optional[str] = None, short: bool = True) -> Dict[str, Any]:
        """Get repository status."""
        args = ["status"]
        if short:
            args.append("--porcelain")
        result = self._run(args, cwd=path or self.repo_path)
        if result.ok:
            lines = result.stdout.split("\n") if result.stdout else []
            files = []
            for line in lines:
                if line.strip():
                    status_code = line[:2]
                    filepath = line[3:]
                    files.append({"status": status_code, "file": filepath})
            return {"ok": True, "files": files, "clean": len(files) == 0}
        return result.to_dict()

    def log(
        self,
        path: Optional[str] = None,
        limit: int = 20,
        oneline: bool = False,
        format_str: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get commit log."""
        args = ["log", f"-{limit}"]

        if format_str:
            args.append(f"--format={format_str}")
        elif oneline:
            args.append("--oneline")
        else:
            # JSON-friendly format
            args.append("--format=%H|%h|%an|%ae|%at|%s")

        result = self._run(args, cwd=path or self.repo_path)
        if result.ok:
            commits = []
            for line in result.stdout.split("\n"):
                if not line.strip():
                    continue
                if oneline or format_str:
                    commits.append(line)
                else:
                    parts = line.split("|", 5)
                    if len(parts) >= 6:
                        commits.append({
                            "hash": parts[0],
                            "short_hash": parts[1],
                            "author_name": parts[2],
                            "author_email": parts[3],
                            "timestamp": parts[4],
                            "subject": parts[5],
                        })
            return {"ok": True, "commits": commits}
        return result.to_dict()

    def branch(
        self,
        path: Optional[str] = None,
        all_branches: bool = False,
        remote: bool = False,
    ) -> Dict[str, Any]:
        """List branches."""
        args = ["branch"]
        if all_branches:
            args.append("-a")
        elif remote:
            args.append("-r")

        result = self._run(args, cwd=path or self.repo_path)
        if result.ok:
            branches = []
            current = None
            for line in result.stdout.split("\n"):
                if not line.strip():
                    continue
                is_current = line.startswith("*")
                branch_name = line.lstrip("* ").strip()
                if is_current:
                    current = branch_name
                branches.append({"name": branch_name, "current": is_current})
            return {"ok": True, "branches": branches, "current": current}
        return result.to_dict()

    def current_branch(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Get current branch name."""
        result = self._run(["branch", "--show-current"], cwd=path or self.repo_path)
        if result.ok:
            return {"ok": True, "branch": result.stdout}
        return result.to_dict()

    def diff(
        self,
        path: Optional[str] = None,
        staged: bool = False,
        file_path: Optional[str] = None,
        stat: bool = False,
    ) -> Dict[str, Any]:
        """Get diff."""
        args = ["diff"]
        if staged:
            args.append("--cached")
        if stat:
            args.append("--stat")
        if file_path:
            args.extend(["--", file_path])

        result = self._run(args, cwd=path or self.repo_path)
        if result.ok:
            return {"ok": True, "diff": result.stdout}
        return result.to_dict()

    def show(
        self,
        commit: str = "HEAD",
        path: Optional[str] = None,
        stat: bool = False,
    ) -> Dict[str, Any]:
        """Show commit details."""
        args = ["show", commit]
        if stat:
            args.append("--stat")

        result = self._run(args, cwd=path or self.repo_path)
        if result.ok:
            return {"ok": True, "content": result.stdout}
        return result.to_dict()

    def remote(self, path: Optional[str] = None, verbose: bool = True) -> Dict[str, Any]:
        """List remotes."""
        args = ["remote"]
        if verbose:
            args.append("-v")

        result = self._run(args, cwd=path or self.repo_path)
        if result.ok:
            remotes = []
            seen = set()
            for line in result.stdout.split("\n"):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    name = parts[0]
                    url = parts[1]
                    remote_type = parts[2] if len(parts) > 2 else ""
                    key = f"{name}|{url}"
                    if key not in seen:
                        seen.add(key)
                        remotes.append({
                            "name": name,
                            "url": url,
                            "type": remote_type.strip("()"),
                        })
            return {"ok": True, "remotes": remotes}
        return result.to_dict()

    def tags(self, path: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """List tags."""
        args = ["tag", "-l", f"--sort=-creatordate"]

        result = self._run(args, cwd=path or self.repo_path)
        if result.ok:
            tags = []
            for line in result.stdout.split("\n")[:limit]:
                if line.strip():
                    tags.append(line.strip())
            return {"ok": True, "tags": tags}
        return result.to_dict()

    def blame(
        self,
        file_path: str,
        path: Optional[str] = None,
        line_range: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, Any]:
        """Get blame information for a file."""
        args = ["blame", "--porcelain"]
        if line_range:
            args.extend(["-L", f"{line_range[0]},{line_range[1]}"])
        args.append(file_path)

        result = self._run(args, cwd=path or self.repo_path)
        if result.ok:
            return {"ok": True, "blame": result.stdout}
        return result.to_dict()

    def stash_list(self, path: Optional[str] = None) -> Dict[str, Any]:
        """List stashes."""
        result = self._run(["stash", "list"], cwd=path or self.repo_path)
        if result.ok:
            stashes = []
            for line in result.stdout.split("\n"):
                if line.strip():
                    stashes.append(line.strip())
            return {"ok": True, "stashes": stashes}
        return result.to_dict()

    def config_get(
        self,
        key: str,
        path: Optional[str] = None,
        global_config: bool = False,
    ) -> Dict[str, Any]:
        """Get a git config value."""
        args = ["config"]
        if global_config:
            args.append("--global")
        args.extend(["--get", key])

        result = self._run(args, cwd=path or self.repo_path)
        if result.ok:
            return {"ok": True, "key": key, "value": result.stdout}
        return {"ok": False, "key": key, "value": None, "error": result.error}

    def fetch(
        self,
        path: Optional[str] = None,
        remote: str = "origin",
        prune: bool = False,
    ) -> Dict[str, Any]:
        """Fetch from remote."""
        args = ["fetch", remote]
        if prune:
            args.append("--prune")

        result = self._run(args, cwd=path or self.repo_path)
        return {
            "ok": result.ok,
            "remote": remote,
            "output": result.stdout or result.stderr,
            "error": result.error,
        }

    def pull(
        self,
        path: Optional[str] = None,
        remote: str = "origin",
        branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Pull from remote."""
        args = ["pull", remote]
        if branch:
            args.append(branch)

        result = self._run(args, cwd=path or self.repo_path)
        return {
            "ok": result.ok,
            "output": result.stdout or result.stderr,
            "error": result.error,
        }

    def checkout(
        self,
        ref: str,
        path: Optional[str] = None,
        create: bool = False,
    ) -> Dict[str, Any]:
        """Checkout a branch or commit."""
        args = ["checkout"]
        if create:
            args.append("-b")
        args.append(ref)

        result = self._run(args, cwd=path or self.repo_path)
        return {
            "ok": result.ok,
            "ref": ref,
            "output": result.stdout or result.stderr,
            "error": result.error,
        }

    def clone(
        self,
        url: str,
        dest: str,
        branch: Optional[str] = None,
        depth: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Clone a repository."""
        args = ["clone", url, dest]
        if branch:
            args.extend(["--branch", branch])
        if depth:
            args.extend(["--depth", str(depth)])

        result = self._run(args, cwd=None, timeout=300)  # 5 min timeout for clone
        return {
            "ok": result.ok,
            "url": url,
            "dest": dest,
            "output": result.stdout or result.stderr,
            "error": result.error,
        }
