"""Shared local git workspaces for the Repositories page.

Repositories are DEFINED FROM THE UI (rows in the `repositories` table);
config carries only the shared ADO instance credentials. Clones live
server-side under REPOS_WORKDIR; edits stay LOCAL — nothing is ever pushed
from this page. Credentials are used only for browse/clone/pull and are
never written into .git/config."""

import re
import shutil
import subprocess
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
}


DEMO_DISCOVERABLE = ["payments-service", "platform-helm", "checkout-service",
                     "notifications-service", "inventory-service"]


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


def _derive_name(url: str) -> str:
    return url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]


def add_repo(db: Session, url: str, name: str, username: str) -> dict:
    from ..db import Repository
    url = url.strip()
    if not re.match(r"^https?://\S+$", url):
        raise RepoError("repository URL must be http(s)")
    if db.query(Repository).filter(Repository.url == url).first():
        raise RepoError("this repository is already defined")
    row = Repository(name=(name.strip() or _derive_name(url)), url=url,
                     added_by=username)
    db.add(row)
    db.commit()
    return {"slot": row.id, "name": row.name, "url": row.url}


def remove_repo(db: Session, slot: int) -> None:
    from ..db import Repository
    row = db.get(Repository, slot)
    if row is None:
        raise RepoError("repository not found")
    workspace = _workdir() / f"{row.id:02d}-{row.name}"
    db.delete(row)
    db.commit()
    shutil.rmtree(workspace, ignore_errors=True)


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
    return _workdir() / f"{repo['slot']:02d}-{repo['name']}"


def _scrub(text: str) -> str:
    if settings.ado_password:
        text = text.replace(settings.ado_password, "***")
    return text


def _git(repo_dir: Path, *args: str, ok_fail: bool = False) -> str:
    p = subprocess.run(["git", *args], cwd=repo_dir,
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0 and not ok_fail:
        raise RepoError(_scrub((p.stderr or p.stdout).strip())[:400])
    return p.stdout


def _authed(repo: dict) -> str:
    url = repo["url"]
    if repo.get("user") and url.startswith("https://"):
        cred = f"{quote(repo['user'], safe='')}:{quote(repo.get('password') or '', safe='')}"
        return url.replace("https://", f"https://{cred}@", 1)
    return url


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
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        shutil.rmtree(repo_dir, ignore_errors=True)
        raise RepoError(_scrub((p.stderr or p.stdout).strip())[:400])
    _git(repo_dir, "remote", "set-url", "origin", repo["url"])  # keep creds out


def pull(slot: int) -> str:
    repo = _repo_by_slot(slot)
    repo_dir = _dir_for(repo)
    if not repo_dir.exists():
        raise RepoError("not cloned yet")
    if settings.demo_mode:
        return "demo repository — nothing to pull"
    return _scrub(_git(repo_dir, "pull", "--ff-only", _authed(repo))) or "up to date"


def discard(slot: int) -> None:
    """Throw away all local edits (checkout + clean)."""
    repo_dir = _dir_for(_repo_by_slot(slot))
    if not repo_dir.exists():
        raise RepoError("not cloned yet")
    _git(repo_dir, "checkout", "--", ".", ok_fail=True)
    _git(repo_dir, "clean", "-fd", ok_fail=True)


# ---------------------------------------------------------------- inspection
def _dirty_paths(repo_dir: Path) -> list[str]:
    out = _git(repo_dir, "status", "--porcelain", ok_fail=True)
    return [line[3:].strip().strip('"') for line in out.splitlines() if line.strip()]


def list_repos() -> list[dict]:
    rows = []
    for repo in configured():
        repo_dir = _dir_for(repo)
        row = {"slot": repo["slot"], "name": repo["name"], "url": repo["url"],
               "cloned": repo_dir.exists(), "branch": "", "last_commit": "",
               "dirty": 0}
        if row["cloned"]:
            row["branch"] = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD",
                                 ok_fail=True).strip()
            row["last_commit"] = _git(repo_dir, "log", "-1", "--format=%s · %an · %cr",
                                      ok_fail=True).strip()
            row["dirty"] = len(_dirty_paths(repo_dir))
        rows.append(row)
    return rows


def tree(slot: int, rel: str = "") -> dict:
    repo_dir = _dir_for(_repo_by_slot(slot))
    if not repo_dir.exists():
        raise RepoError("not cloned yet")
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


def read_file(slot: int, rel: str) -> dict:
    repo_dir = _dir_for(_repo_by_slot(slot))
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


def write_file(slot: int, rel: str, content: str) -> None:
    repo_dir = _dir_for(_repo_by_slot(slot))
    if not repo_dir.exists():
        raise RepoError("not cloned yet")
    target = _safe(repo_dir, rel)
    if len(content.encode()) > MAX_FILE_BYTES:
        raise RepoError("content too large")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def diff(slot: int, rel: str = "") -> str:
    repo_dir = _dir_for(_repo_by_slot(slot))
    if not repo_dir.exists():
        raise RepoError("not cloned yet")
    args = ["diff"] + (["--", rel] if rel else [])
    return _git(repo_dir, *args, ok_fail=True)
