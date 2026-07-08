from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from fastapi.responses import StreamingResponse

from ..auth.rbac import User
from ..auth.sessions import admin_user
from ..providers import impl

router = APIRouter(prefix="/architecture", tags=["architecture"])


def _projects(csv: str) -> list[str]:
    return [p.strip() for p in csv.split(",") if p.strip()]


@router.get("/envs")
def envs(user: User = Depends(admin_user)):
    return impl("architecture").envs(user)


@router.get("/model")
def model(env: str = "prd", projects: str = "", app: str = "",
          user: User = Depends(admin_user)):
    return impl("architecture").model(user, env, _projects(projects), app or None)


@router.get("/diff")
def diff(envA: str = "dev", envB: str = "prd", projects: str = "",
         user: User = Depends(admin_user)):
    return impl("architecture").diff(user, envA, envB, _projects(projects))


@router.post("/discover")
def discover(payload: dict = Body(default={}), user: User = Depends(admin_user)):
    gen = impl("architecture").discover(
        user,
        payload.get("env") or "prd",
        [p for p in (payload.get("projects") or []) if p],
    )
    return StreamingResponse(gen, media_type="text/event-stream")
