from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from langchain_core.messages import BaseMessage

from src.ai.agents.datagen_agent import run_agent


app = FastAPI(title="datagen-agent", version="1.0")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    ok: bool
    response: str


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"ok": True}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # Current agent is stateless; keep history empty
    msg = run_agent(req.message, history=[])
    return ChatResponse(ok=True, response=str(getattr(msg, "content", msg)))
