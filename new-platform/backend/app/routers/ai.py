from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth.rbac import User
from ..auth.sessions import current_user
from ..providers import impl

router = APIRouter(prefix="/ai", tags=["ai"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/incidents")
def incidents(user: User = Depends(current_user)):
    return impl("ai").incidents(user)


@router.post("/incidents/{incident_id}/analyze")
def analyze_incident(incident_id: int, user: User = Depends(current_user)):
    gen = impl("ai").analyze_incident(user, incident_id)
    return StreamingResponse(gen, media_type="text/event-stream", headers=_SSE_HEADERS)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    persona: str = "developer"


@router.post("/assistant/chat")
def assistant_chat(body: ChatBody, user: User = Depends(current_user)):
    msgs = [m.model_dump() for m in body.messages]
    gen = impl("ai").assistant_chat(user, msgs, body.persona)
    return StreamingResponse(gen, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/assistant/sources")
def assistant_sources(user: User = Depends(current_user)):
    return impl("ai").assistant_sources(user)


@router.get("/assistant/stats")
def assistant_stats(user: User = Depends(current_user)):
    return impl("ai").assistant_stats(user)
