from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from src.ai.agents.kubernetes_agent import build_kubernetes_agent


app = FastAPI(title="kubernetes-agent", version="1.0")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    ok: bool
    response: str
    tool_calls: List[Dict[str, Any]] = []


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"ok": True}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    agent = build_kubernetes_agent()
    result = agent.run(req.message, history=None)

    return ChatResponse(
        ok=True,
        response=str(result.get("final_response", "")),
        tool_calls=list(result.get("tool_calls", [])),
    )
