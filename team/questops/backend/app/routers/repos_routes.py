"""Repositories page API: clone/pull/discard + explore/edit local workspaces.
Edits never leave the server — there is deliberately no push endpoint."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..db import User
from ..integrations import repos
from ..integrations.repos import RepoError

router = APIRouter(prefix="/api/repos", tags=["repos"])


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except RepoError as exc:
        raise HTTPException(400, str(exc))


@router.get("")
def list_repos(user: User = Depends(current_user)):
    return {"repos": _wrap(repos.list_repos)}


@router.post("/{slot}/clone")
def clone(slot: int, user: User = Depends(current_user)):
    _wrap(repos.clone, slot)
    return {"ok": True}


@router.post("/{slot}/pull")
def pull(slot: int, user: User = Depends(current_user)):
    return {"output": _wrap(repos.pull, slot)}


@router.post("/{slot}/discard")
def discard(slot: int, user: User = Depends(current_user)):
    _wrap(repos.discard, slot)
    return {"ok": True}


@router.get("/{slot}/tree")
def tree(slot: int, path: str = "", user: User = Depends(current_user)):
    return _wrap(repos.tree, slot, path)


@router.get("/{slot}/file")
def read_file(slot: int, path: str, user: User = Depends(current_user)):
    return _wrap(repos.read_file, slot, path)


class WriteBody(BaseModel):
    path: str
    content: str


@router.put("/{slot}/file")
def write_file(slot: int, body: WriteBody, user: User = Depends(current_user)):
    _wrap(repos.write_file, slot, body.path, body.content)
    return {"ok": True}


@router.get("/{slot}/diff")
def diff(slot: int, path: str = "", user: User = Depends(current_user)):
    return {"diff": _wrap(repos.diff, slot, path)}
