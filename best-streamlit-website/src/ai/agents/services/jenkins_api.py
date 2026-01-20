from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from src.ai.agents.jenkins_agent import build_jenkins_agent
from src.ai.mcp_servers.jenkins.config import JenkinsMCPServerConfig
from src.config_utils import env_str


app = FastAPI(title="jenkins-agent", version="1.0")


class ChatRequest(BaseModel):
    message: str
    user_name: Optional[str] = None


class ChatResponse(BaseModel):
    ok: bool
    response: str
    tool_calls: List[Dict[str, Any]] = []


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"ok": True}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    cfg = JenkinsMCPServerConfig.from_env()

    ollama_base_url = env_str("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = env_str("OLLAMA_MODEL", "qwen2.5:7b-instruct-q6_K")

    agent = build_jenkins_agent(
        base_url=cfg.base_url,
        username=None,
        api_token=None,
        verify_ssl=cfg.verify_ssl,
        model=ollama_model,
        llm_base_url=ollama_base_url,
        user_name=req.user_name,
    )

    # Respect env-first Ollama settings via OLLAMA_* variables
    # (build_jenkins_agent still accepts parameters; Streamlit uses env defaults)
    result = agent.run(req.message, history=None)

    return ChatResponse(
        ok=True,
        response=str(result.get("final_response", "")),
        tool_calls=list(result.get("tool_calls", [])),
    )
