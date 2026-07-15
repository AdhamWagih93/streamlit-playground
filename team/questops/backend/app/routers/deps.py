"""Dependency-matrix page: pipelines -> playbooks/roles/scripts, used vs unused."""

import time

from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..db import User
from ..integrations import depmatrix, jenkins
from ..integrations.repos import RepoError

router = APIRouter(prefix="/api", tags=["deps"])

_CACHE: dict = {}  # (slot, username) -> {at, payload}
TTL = 120


@router.get("/deps")
def deps(slot: int, refresh: bool = False, user: User = Depends(current_user)):
    key = (slot, user.username)
    hit = _CACHE.get(key)
    if hit and not refresh and time.time() - hit["at"] < TTL:
        return {**hit["payload"], "cached": True}
    if refresh:  # re-analyze means re-ask Jenkins too
        jenkins.invalidate_script_paths()
    try:
        payload = depmatrix.analyze(slot, user.username)
    except RepoError as exc:
        raise HTTPException(400, str(exc))
    _CACHE[key] = {"at": time.time(), "payload": payload}
    return {**payload, "cached": False}
