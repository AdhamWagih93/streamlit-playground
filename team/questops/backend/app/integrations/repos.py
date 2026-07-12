"""Per-user git workspaces for the Repositories page.

Repositories are DEFINED FROM THE UI (rows in the `repositories` table);
config carries only the shared ADO instance credentials. The clone under
REPOS_WORKDIR is the SERVER COPY (nobody edits it); every logged-in member
gets their own git worktree next to it ({id}-{name}.wt/{username}) so edits
never overlap. Edits stay LOCAL — nothing is ever pushed from this page.
Credentials are used only for browse/clone/fetch and are never written
into .git/config."""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests
from sqlalchemy.orm import Session

from ..config import settings

MAX_FILE_BYTES = 512 * 1024


class RepoError(Exception):
    pass


def _workdir() -> Path:
    p = Path(settings.repos_workdir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


DEMO_REPO_FILES = {
    "payments-service": {
        "README.md": "# payments-service\n\nHandles card and wallet payments.\n",
        "Dockerfile": "FROM eclipse-temurin:21-jre\nCOPY app.jar /app.jar\nENTRYPOINT [\"java\",\"-jar\",\"/app.jar\"]\n",
        "Jenkinsfile": "pipeline {\n  agent { label 'java' }\n  stages {\n    stage('Build') { steps { sh './gradlew build' } }\n    stage('Unit Tests') { steps { sh './gradlew test' } }\n  }\n}\n",
        "src/main.py": "def charge(amount: int) -> bool:\n    return amount > 0\n",
        "requirements.txt": "fastapi==0.110.0\nrequests==2.31.0\n",
        "helm/values.yaml": "replicaCount: 2\nimage:\n  repository: registry.local/payments\n  tag: 1.4.2\n",
    },
    "platform-helm": {
        "README.md": "# platform-helm\n\nShared helm charts for the platform.\n",
        "charts/app/Chart.yaml": "apiVersion: v2\nname: app\nversion: 0.1.0\n",
        "charts/app/values.yaml": "replicaCount: 1\nresources:\n  requests: { cpu: 100m, memory: 128Mi }\n",
        "charts/app/templates/deployment.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{ .Release.Name }}\n",
    },
    "Engine": {
        "README.md": "# Engine\n\nAll Jenkins pipeline definitions (groovy) live here.\n",
        "pipelines/payments-service.groovy":
            "pipeline {\n  agent { label 'java' }\n  stages {\n"
            "    stage('Build') { steps { sh './gradlew assemble' } }\n"
            "    stage('Unit Tests') { steps { sh './gradlew test' } }  "
            "// testcontainers: needs the docker daemon\n"
            "    stage('Package') { steps { sh './gradlew jibDockerBuild' } }\n"
            "  }\n}\n",
        "pipelines/checkout-service.groovy":
            "pipeline {\n  agent { label 'java' }\n  stages {\n"
            "    stage('Build') { steps { sh './gradlew build' } }\n  }\n}\n",
    },
}


DEMO_DISCOVERABLE = ["Engine", "UI", "inventories", "ocp-templates",
                     "payments-service", "platform-helm", "checkout-service",
                     "notifications-service"]


def configured() -> list[dict]:
    """The UI-defined repositories; every one clones with the ADO creds."""
    from ..db import Repository, SessionLocal
    db = SessionLocal()
    try:
        rows = db.query(Repository).order_by(Repository.id).all()
    finally:
        db.close()
    return [{"slot": r.id, "name": r.name, "url": r.url, "added_by": r.added_by,
             "user": settings.ado_user, "password": settings.ado_password}
            for r in rows]


def add_repo(db: Session, url: str, name: str, username: str) -> dict:
    from ..db import Repository
    url = url.strip()
    name = name.strip()
    if not re.match(r"^https?://\S+$", url):
        raise RepoError("repository URL must be http(s)")
    if not name:
        raise RepoError("repository name is required "
                        "(e.g. inventories, Engine, UI, ocp-templates)")
    if db.query(Repository).filter(Repository.url == url).first():
        raise RepoError("this repository is already defined")
    if db.query(Repository).filter(Repository.name.ilike(name)).first():
        raise RepoError(f"a repository named '{name}' is already defined")
    row = Repository(name=name, url=url, added_by=username)
    db.add(row)
    db.commit()
    return {"slot": row.id, "name": row.name, "url": row.url}


def remove_repo(db: Session, slot: int) -> None:
    from ..db import Repository
    row = db.get(Repository, slot)
    if row is None:
        raise RepoError("repository not found")
    base = _workdir() / f"{row.id:02d}-{row.name}"
    worktrees = _workdir() / f"{row.id:02d}-{row.name}.wt"
    db.delete(row)
    db.commit()
    shutil.rmtree(worktrees, ignore_errors=True)  # members' workspaces too
    shutil.rmtree(base, ignore_errors=True)


def discover() -> list[dict]:
    """Browse the ADO instance for repositories to add."""
    if settings.demo_mode:
        return [{"name": n, "project": "Platform",
                 "url": f"https://git.example.local/platform/{n}.git"}
                for n in DEMO_DISCOVERABLE]
    if not settings.ado_url:
        raise RepoError("ADO_URL is not configured")
    try:
        r = requests.get(f"{settings.ado_url.rstrip('/')}/_apis/git/repositories",
                         params={"api-version": "6.0"},
                         auth=(settings.ado_user, settings.ado_password),
                         timeout=20)
        r.raise_for_status()
        items = r.json().get("value", [])
    except (requests.RequestException, ValueError) as exc:
        raise RepoError(f"ADO browse failed: {_scrub(str(exc))[:200]}")
    return sorted(({"name": i.get("name", ""),
                    "project": (i.get("project") or {}).get("name", ""),
                    "url": i.get("remoteUrl") or i.get("webUrl", "")}
                   for i in items if not i.get("isDisabled")),
                  key=lambda x: (x["project"].lower(), x["name"].lower()))


def _repo_by_slot(slot: int) -> dict:
    for r in configured():
        if r["slot"] == slot:
            return r
    raise RepoError(f"repo slot {slot} is not configured")


def _dir_for(repo: dict) -> Path:
    """The server copy — the plain clone nobody edits directly."""
    return _workdir() / f"{repo['slot']:02d}-{repo['name']}"


def _safe_user(username: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", username or "").strip("._-") or "user"


def _worktree_root(repo: dict) -> Path:
    return _workdir() / f"{repo['slot']:02d}-{repo['name']}.wt"


def _ensure_worktree(repo: dict, username: str) -> Path:
    """Each member works in their own detached worktree (shared objects,
    isolated files) so teammates never step on each other's edits."""
    base = _dir_for(repo)
    if not base.exists():
        raise RepoError("not cloned yet")
    wt = _worktree_root(repo) / _safe_user(username)
    if not wt.exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        _git(base, "worktree", "add", "--detach", str(wt), "HEAD")
    return wt


def _workspace(repo: dict, username: str | None) -> Path:
    """username=None -> the server copy (read-only callers like the Failure
    Dive); a username -> that member's own worktree (created on demand)."""
    if username:
        return _ensure_worktree(repo, username)
    base = _dir_for(repo)
    if not base.exists():
        raise RepoError("not cloned yet")
    return base


def _scrub(text: str) -> str:
    if settings.ado_password:
        text = text.replace(settings.ado_password, "***")
    return text


# never let git prompt for credentials: there is no terminal in the container,
# so prompting surfaces as "could not read Username ... No such device or
# address". With prompts off, git fails fast with a message we can hint on.
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}

_AUTH_HINT = (" — git asked for credentials: check ADO_USER / ADO_PASSWORD "
              "(PAT) in config (compose: QO_ADO_USER / QO_ADO_PASSWORD); "
              "QuestOps injects them into http(s) URLs per command")


def _with_hint(msg: str) -> str:
    if ("could not read Username" in msg or "could not read Password" in msg
            or "Authentication failed" in msg or "terminal prompts disabled" in msg):
        msg += _AUTH_HINT
    return msg


def _git(repo_dir: Path, *args: str, ok_fail: bool = False) -> str:
    p = subprocess.run(["git", *args], cwd=repo_dir, env=_GIT_ENV,
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0 and not ok_fail:
        raise RepoError(_with_hint(_scrub((p.stderr or p.stdout).strip())[:400]))
    return p.stdout


def _authed(repo: dict) -> str:
    """Inject the ADO creds into the URL for this one command. Handles both
    http and https (on-prem ADO is often plain http) and URLs that already
    embed a username (ADO's remoteUrl usually does: https://user@host/...)."""
    url = repo["url"]
    m = re.match(r"^(https?://)(?:[^/@]+@)?(.+)$", url)
    if not m or not repo.get("user"):
        return url
    scheme, rest = m.groups()
    cred = f"{quote(repo['user'], safe='')}:{quote(repo.get('password') or '', safe='')}"
    return f"{scheme}{cred}@{rest}"


def _safe(repo_dir: Path, rel: str) -> Path:
    target = (repo_dir / rel).resolve() if rel else repo_dir.resolve()
    root = repo_dir.resolve()
    if target != root and root not in target.parents:
        raise RepoError("path escapes the repository")
    if target != root and ".git" in target.relative_to(root).parts:
        raise RepoError(".git is off limits")
    return target


# ---------------------------------------------------------------- lifecycle
def _seed_demo_repo(repo: dict, repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    files = DEMO_REPO_FILES.get(repo["name"]) or {
        "README.md": f"# {repo['name']}\n\nDemo repository added from the UI.\n",
        "src/app.py": "def main():\n    print('hello')\n",
    }
    for rel, content in files.items():
        f = repo_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    try:  # a real git history makes status/diff work in the demo too
        _git(repo_dir, "init")
        _git(repo_dir, "add", "-A")
        _git(repo_dir, "-c", "user.email=demo@questops", "-c", "user.name=QuestOps Demo",
             "commit", "-m", "seed demo repository")
    except (RepoError, FileNotFoundError):
        pass  # git missing: page still works, just without status/diff


def clone(slot: int) -> None:
    repo = _repo_by_slot(slot)
    repo_dir = _dir_for(repo)
    if repo_dir.exists():
        raise RepoError("already cloned — use pull to update")
    if settings.demo_mode:
        _seed_demo_repo(repo, repo_dir)
        return
    p = subprocess.run(["git", "clone", _authed(repo), str(repo_dir)],
                       env=_GIT_ENV, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        shutil.rmtree(repo_dir, ignore_errors=True)
        raise RepoError(_with_hint(_scrub((p.stderr or p.stdout).strip())[:400]))
    _git(repo_dir, "remote", "set-url", "origin", repo["url"])  # keep creds out


def pull(slot: int, username: str | None = None) -> str:
    """Update the server copy from origin, then fast-forward the member's
    worktree to it (git refuses if their local edits would be clobbered)."""
    repo = _repo_by_slot(slot)
    base = _dir_for(repo)
    if not base.exists():
        raise RepoError("not cloned yet")
    out = ""
    if not settings.demo_mode:
        out = _scrub(_git(base, "pull", "--ff-only", _authed(repo))).strip()
    if username:
        wt = _ensure_worktree(repo, username)
        base_head = _git(base, "rev-parse", "HEAD").strip()
        wt_head = _git(wt, "rev-parse", "HEAD").strip()
        if wt_head != base_head:
            try:
                _git(wt, "checkout", "--detach", base_head)
                out += f"\nyour workspace moved to {base_head[:8]}"
            except RepoError as exc:
                raise RepoError("server copy updated, but your workspace has "
                                f"local edits that conflict: {exc}")
        else:
            out += "\nyour workspace is already at the server copy"
    return out.strip() or "up to date"


def discard(slot: int, username: str | None = None) -> None:
    """Throw away the member's local edits (checkout + clean in THEIR worktree)."""
    wt = _workspace(_repo_by_slot(slot), username)
    _git(wt, "checkout", "--", ".", ok_fail=True)
    _git(wt, "clean", "-fd", ok_fail=True)


_FETCH_AT: dict[int, float] = {}
_FETCH_TTL = 90  # seconds between real fetches per repo


def remote_status(slot: int, username: str | None = None) -> dict:
    """What changed on the server: throttled fetch + behind counts + the
    incoming commits. Cheap enough for the page to poll."""
    repo = _repo_by_slot(slot)
    base = _dir_for(repo)
    if not base.exists():
        raise RepoError("not cloned yet")
    fetch_error = None
    if not settings.demo_mode and time.time() - _FETCH_AT.get(slot, 0) > _FETCH_TTL:
        try:  # authed URL on the command line; origin's config stays cred-free
            _git(base, "fetch", _authed(repo), "+refs/heads/*:refs/remotes/origin/*")
            _FETCH_AT[slot] = time.time()
        except RepoError as exc:
            fetch_error = _scrub(str(exc))[:200]
    branch = _git(base, "rev-parse", "--abbrev-ref", "HEAD", ok_fail=True).strip()
    upstream = f"origin/{branch}" if branch and branch != "HEAD" else ""
    behind, incoming = 0, []
    if upstream and _git(base, "rev-parse", "--verify", upstream, ok_fail=True).strip():
        behind = int(_git(base, "rev-list", "--count", f"HEAD..{upstream}",
                          ok_fail=True).strip() or 0)
        if behind:
            raw = _git(base, "log", "--format=%h\x1f%an\x1f%ct\x1f%s", "-10",
                       f"HEAD..{upstream}", ok_fail=True)
            for line in raw.splitlines():
                p = line.split("\x1f", 3)
                if len(p) == 4:
                    incoming.append({"short": p[0], "author": p[1],
                                     "at": int(p[2]), "subject": p[3]})
    wt_pending = 0
    if username:
        wt = _worktree_root(repo) / _safe_user(username)
        if wt.exists():
            base_head = _git(base, "rev-parse", "HEAD").strip()
            wt_pending = int(_git(wt, "rev-list", "--count",
                                  f"HEAD..{base_head}", ok_fail=True).strip() or 0)
    return {"branch": branch, "behind": behind, "incoming": incoming,
            "wt_pending": wt_pending, "fetch_error": fetch_error,
            "checked_at": time.time()}


def history(slot: int, username: str | None = None, path: str = "",
            limit: int = 30) -> dict:
    """Commit history (optionally for one path), from the member's workspace."""
    repo = _repo_by_slot(slot)
    d = _workspace(repo, username)
    if path:
        _safe(d, path)
    args = ["log", "--format=%h\x1f%H\x1f%an\x1f%ct\x1f%s", f"-{min(int(limit), 100)}"]
    if path:
        args += ["--", path]
    commits = []
    for line in _git(d, *args, ok_fail=True).splitlines():
        p = line.split("\x1f", 4)
        if len(p) == 5:
            commits.append({"short": p[0], "sha": p[1], "author": p[2],
                            "at": int(p[3]), "subject": p[4]})
    return {"commits": commits, "path": path}


MAX_DIFF_BYTES = 60_000


def commit_diff(slot: int, sha: str, username: str | None = None) -> str:
    """Full patch for one commit — fetched on demand from the UI."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        raise RepoError("invalid commit id")
    d = _workspace(_repo_by_slot(slot), username)
    out = _git(d, "show", "--stat", "--patch",
               "--format=commit %H%nAuthor: %an <%ae>%nDate:   %ci%n%n    %s%n", sha)
    if len(out) > MAX_DIFF_BYTES:
        out = out[:MAX_DIFF_BYTES] + f"\n… (truncated at {MAX_DIFF_BYTES} chars)"
    return out


# ---------------------------------------------------------------- inspection
def _dirty_paths(repo_dir: Path) -> list[str]:
    out = _git(repo_dir, "status", "--porcelain", ok_fail=True)
    return [line[3:].strip().strip('"') for line in out.splitlines() if line.strip()]


def list_repos(username: str | None = None) -> list[dict]:
    rows = []
    for repo in configured():
        base = _dir_for(repo)
        row = {"slot": repo["slot"], "name": repo["name"], "url": repo["url"],
               "cloned": base.exists(), "branch": "", "last_commit": "",
               "dirty": 0}
        if row["cloned"]:
            row["branch"] = _git(base, "rev-parse", "--abbrev-ref", "HEAD",
                                 ok_fail=True).strip()
            row["last_commit"] = _git(base, "log", "-1", "--format=%s · %an · %cr",
                                      ok_fail=True).strip()
            if username:  # dirty = THIS member's local edits, in their worktree
                wt = _worktree_root(repo) / _safe_user(username)
                row["dirty"] = len(_dirty_paths(wt)) if wt.exists() else 0
        rows.append(row)
    return rows


def tree(slot: int, rel: str = "", username: str | None = None) -> dict:
    repo_dir = _workspace(_repo_by_slot(slot), username)
    target = _safe(repo_dir, rel)
    if not target.is_dir():
        raise RepoError(f"not a directory: {rel}")
    dirty = set(_dirty_paths(repo_dir))
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if p.name == ".git":
            continue
        rp = str(p.relative_to(repo_dir))
        entries.append({
            "name": p.name, "path": rp,
            "type": "dir" if p.is_dir() else "file",
            "size": p.stat().st_size if p.is_file() else None,
            "dirty": rp in dirty or (p.is_dir() and any(d.startswith(rp + "/") for d in dirty)),
        })
    return {"path": rel, "entries": entries}


def read_file(slot: int, rel: str, username: str | None = None) -> dict:
    repo_dir = _workspace(_repo_by_slot(slot), username)
    target = _safe(repo_dir, rel)
    if not target.is_file():
        raise RepoError(f"not a file: {rel}")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise RepoError(f"file larger than {MAX_FILE_BYTES // 1024}KB — edit it outside QuestOps")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise RepoError("binary file — cannot edit here")
    return {"path": rel, "content": content}


def write_file(slot: int, rel: str, content: str,
               username: str | None = None) -> None:
    repo_dir = _workspace(_repo_by_slot(slot), username)
    target = _safe(repo_dir, rel)
    if len(content.encode()) > MAX_FILE_BYTES:
        raise RepoError("content too large")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def diff(slot: int, rel: str = "", username: str | None = None) -> str:
    repo_dir = _workspace(_repo_by_slot(slot), username)
    args = ["diff"] + (["--", rel] if rel else [])
    return _git(repo_dir, *args, ok_fail=True)
