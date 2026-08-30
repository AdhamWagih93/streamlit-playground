"""Configurations hub — architecture from the Control project's team config repos."""

from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..db import User
from ..integrations import archconfig
from ..integrations.repos import _dir_for, control_repos_lookup

router = APIRouter(prefix="/api/configs", tags=["configs"])


@router.get("")
def configs(refresh: bool = False, user: User = Depends(current_user)):
    return archconfig.analyze(refresh=refresh)


@router.get("/file")
def config_file(team: str, path: str, user: User = Depends(current_user)):
    """One config.yml, credentials masked — read-only."""
    repo = control_repos_lookup(team)
    if not repo:
        raise HTTPException(404, f"no Control repo named {team}")
    root = _dir_for(repo).resolve()
    f = (root / path).resolve()
    if root not in f.parents or not f.is_file():
        raise HTTPException(404, "file not found")
    return {"team": team, "path": path,
            "text": archconfig.redact(f.read_text(encoding="utf-8", errors="replace"))[:200000]}


@router.get("/overview")
def configs_overview(refresh: bool = False, user: User = Depends(current_user)):
    return archconfig.overview(refresh=refresh)


@router.get("/project/{name}")
def configs_project(name: str, refresh: bool = False, user: User = Depends(current_user)):
    return archconfig.project_detail(name, refresh=refresh)
