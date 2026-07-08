from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth.rbac import User
from ..auth.sessions import current_user
from ..providers import impl

router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("/jenkins")
def jenkins_status(user: User = Depends(current_user)):
    return impl("actions").jenkins_status(user)


@router.get("/candidates")
def candidates(user: User = Depends(current_user)):
    return impl("actions").candidates(user)


class TriggerBody(BaseModel):
    pipeline: str
    params: dict = Field(default_factory=dict)


@router.post("/trigger")
def trigger(body: TriggerBody, user: User = Depends(current_user)):
    return impl("actions").trigger(user, body.pipeline, body.params)
