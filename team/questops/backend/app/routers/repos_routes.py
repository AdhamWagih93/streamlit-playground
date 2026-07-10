"""Repositories page API: define repos from the UI (ADO creds from config),
clone/pull/discard + explore/edit local workspaces.
Edits never leave the server — there is deliberately no push endpoint."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import User, get_db
from ..integrations import repo_agent, repo_scan, repos
from ..integrations.repos import RepoError

router = APIRouter(prefix="/api/repos", tags=["repos"])


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except RepoError as exc:
        raise HTTPException(400, str(exc))


@router.get("")
def list_repos(user: User = Depends(current_user)):
    return {"repos": _wrap(repos.list_repos, user.username)}


class AddRepoBody(BaseModel):
    url: str
    name: str = ""


@router.post("")
def add_repo(body: AddRepoBody, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    return {"repo": _wrap(repos.add_repo, db, body.url, body.name, user.username)}


@router.get("/discover")
def discover(user: User = Depends(current_user)):
    """Browse the configured ADO instance for repositories to add."""
    return {"repos": _wrap(repos.discover)}


@router.delete("/{slot}")
def remove_repo(slot: int, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    _wrap(repos.remove_repo, db, slot)
    return {"ok": True}


@router.post("/{slot}/clone")
def clone(slot: int, user: User = Depends(current_user)):
    _wrap(repos.clone, slot)
    return {"ok": True}


@router.post("/{slot}/pull")
def pull(slot: int, user: User = Depends(current_user)):
    return {"output": _wrap(repos.pull, slot, user.username)}


@router.post("/{slot}/discard")
def discard(slot: int, user: User = Depends(current_user)):
    _wrap(repos.discard, slot, user.username)
    return {"ok": True}


@router.get("/{slot}/tree")
def tree(slot: int, path: str = "", user: User = Depends(current_user)):
    return _wrap(repos.tree, slot, path, user.username)


@router.get("/{slot}/file")
def read_file(slot: int, path: str, user: User = Depends(current_user)):
    return _wrap(repos.read_file, slot, path, user.username)


class WriteBody(BaseModel):
    path: str
    content: str


@router.put("/{slot}/file")
def write_file(slot: int, body: WriteBody, user: User = Depends(current_user)):
    _wrap(repos.write_file, slot, body.path, body.content, user.username)
    return {"ok": True}


@router.get("/{slot}/diff")
def diff(slot: int, path: str = "", user: User = Depends(current_user)):
    return {"diff": _wrap(repos.diff, slot, path, user.username)}


@router.get("/{slot}/remote")
def remote(slot: int, user: User = Depends(current_user)):
    """Server-side changes: throttled fetch + behind counts + incoming commits."""
    return _wrap(repos.remote_status, slot, user.username)


@router.get("/{slot}/history")
def history(slot: int, path: str = "", limit: int = 30,
            user: User = Depends(current_user)):
    return _wrap(repos.history, slot, user.username, path, limit)


@router.get("/{slot}/commit/{sha}")
def commit_diff(slot: int, sha: str, user: User = Depends(current_user)):
    return {"sha": sha, "diff": _wrap(repos.commit_diff, slot, sha, user.username)}


@router.get("/{slot}/scan")
def scan(slot: int, user: User = Depends(current_user)):
    """Deterministic technology detection + recommendations."""
    return _wrap(repo_scan.scan, slot, user.username)


class AgentBody(BaseModel):
    message: str
    history: list[dict] = []
    allow_write: bool = False


@router.post("/{slot}/agent")
def agent(slot: int, body: AgentBody, user: User = Depends(current_user),
          db: Session = Depends(get_db)):
    """Start an agent turn. The agent only PROPOSES tool calls — they come
    back as pending commands that a human must approve via /agent/decide."""
    if not body.message.strip():
        raise HTTPException(400, "message is required")
    return _wrap(repo_agent.start, db, slot, user.username,
                 body.message.strip(), body.history, body.allow_write)


class DecideBody(BaseModel):
    command_id: int
    approve: bool


@router.post("/agent/decide")
def agent_decide(body: DecideBody, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Approve (execute) or deny one proposed agent command; the decision and
    any output land in the agent_commands audit log."""
    return _wrap(repo_agent.decide, db, body.command_id, body.approve,
                 user.username)


@router.get("/{slot}/agent/log")
def agent_log(slot: int, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    return {"log": repo_agent.audit_log(db, slot)}
