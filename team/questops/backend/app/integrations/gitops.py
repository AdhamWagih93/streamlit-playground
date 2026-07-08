"""Repo actions: AI drafts a change from a prompt template; a human
approves before anything is pushed. Execution never happens without an
approved RepoAction row."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import settings
from ..db import PromptTemplate, RepoAction, utcnow
from . import ollama

PLAN_SYSTEM = (
    "You are a senior platform engineer. Given a task template and parameters, "
    "produce a minimal, safe change to a git repository. Reply ONLY with JSON: "
    '{"plan": "<short human-readable plan, markdown bullets>", '
    '"commit_message": "<conventional commit message>", '
    '"files": [{"path": "<repo-relative path>", "content": "<FULL new file content>"}]}. '
    "Touch as few files as possible. Never include secrets."
)


def render_template(template: PromptTemplate, params: dict) -> str:
    body = template.body
    for k, v in (params or {}).items():
        body = body.replace("{{" + k + "}}", str(v))
    return body


def extract_variables(body: str) -> list[str]:
    return sorted(set(re.findall(r"\{\{(\w+)\}\}", body)))


def generate_plan(template: PromptTemplate, params: dict, repo_url: str, branch: str) -> dict:
    prompt = render_template(template, params)
    user_msg = (f"Repository: {repo_url}\nTarget branch: {branch}\n\nTask:\n{prompt}")
    try:
        reply = ollama.chat([{"role": "user", "content": user_msg}],
                            system=PLAN_SYSTEM, json_mode=True)
        data = ollama.extract_json(reply)
        return {
            "plan": str(data.get("plan", "")).strip() or "(model returned no plan)",
            "commit_message": str(data.get("commit_message", ""))[:300]
            or f"chore: {template.name}",
            "files": [{"path": f["path"], "content": f["content"]}
                      for f in data.get("files", []) if f.get("path")],
        }
    except (ollama.OllamaUnavailable, ValueError, KeyError, TypeError) as exc:
        # Offline fallback keeps the approval flow demoable end-to-end.
        return {
            "plan": (f"*(AI offline — placeholder plan: {exc})*\n"
                     f"- Apply template **{template.name}** to `{repo_url}` on `{branch}`\n"
                     f"- Params: `{params}`\n- Add a marker file documenting the intent"),
            "commit_message": f"chore: {template.name} (questops)",
            "files": [{"path": ".questops/last-action.md",
                       "content": f"# {template.name}\n\n{prompt}\n"}],
        }


def _authed_url(repo_url: str) -> str:
    if settings.git_token and repo_url.startswith("https://"):
        return repo_url.replace(
            "https://", f"https://{settings.git_user_name}:{settings.git_token}@", 1)
    return repo_url


def _scrub(text: str) -> str:
    return text.replace(settings.git_token, "***") if settings.git_token else text


def execute(action: RepoAction) -> str:
    """Runs ONLY after human approval (enforced by the route)."""
    if settings.demo_mode:
        files = "\n".join(f"  wrote {f['path']}" for f in action.files)
        return (f"[demo] simulated push to {action.repo_url} ({action.branch})\n"
                f"{files}\n  commit: {action.commit_message}\n[demo] nothing left the machine")

    workdir = Path(tempfile.mkdtemp(prefix="questops-"))
    log: list[str] = []

    def run(*cmd: str, cwd: Path) -> None:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300)
        log.append(_scrub(f"$ {' '.join(cmd)}\n{p.stdout}{p.stderr}".strip()))
        if p.returncode != 0:
            raise RuntimeError(_scrub(f"{cmd[0]} failed: {p.stderr[:500]}"))

    try:
        run("git", "clone", "--depth", "1", _authed_url(action.repo_url), "repo", cwd=workdir)
        repo = workdir / "repo"
        run("git", "config", "user.name", settings.git_user_name, cwd=repo)
        run("git", "config", "user.email", settings.git_user_email, cwd=repo)
        branch = action.branch or f"questops/action-{action.id}"
        run("git", "checkout", "-b", branch, cwd=repo)
        for f in action.files:
            target = (repo / f["path"]).resolve()
            if not str(target).startswith(str(repo.resolve())):
                raise RuntimeError(f"path escapes repo: {f['path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f["content"])
        run("git", "add", "-A", cwd=repo)
        run("git", "commit", "-m", action.commit_message or f"questops action #{action.id}",
            cwd=repo)
        run("git", "push", "-u", "origin", branch, cwd=repo)
        log.append(f"pushed branch '{branch}' — open a PR to merge")
        return "\n".join(log)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
