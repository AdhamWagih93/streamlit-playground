"""AI slice — live provider.

`assistant_chat` streams from the on-prem Ollama endpoint (DOCCHAT_OLLAMA_URL /
DOCCHAT_MODEL). Incident evidence gathering against the live integrations
(orchestrator logs, ES event correlation, config-repo diffs) is not wired yet,
so those endpoints raise IntegrationUnavailable — never fake data in live mode.

Function signatures mirror app/providers/demo/ai.py exactly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ...auth.rbac import User
from ...config import get_settings
from .clients import IntegrationUnavailable

# In-memory audit log of assistant exchanges (process lifetime).
_CHAT_LOG: list[dict] = []

_PERSONA_HINTS = {
    "developer": ("The user is a developer: answer with concrete integration details, "
                  "auth flows, endpoints and code-level guidance."),
    "analyst": ("The user is a business analyst: answer with scope, requirements, "
                "dependencies and gaps, structured like a BRD section."),
    "tester": ("The user is a QA/tester: answer with concrete, numbered test cases "
               "including edge cases."),
}


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------- incidents
def incidents(user: User) -> list[dict]:
    raise IntegrationUnavailable(
        "Incident analysis",
        "live evidence sources (pipeline orchestrator / ES deployments) not wired yet",
    )


def analyze_incident(user: User, incident_id: int):
    raise IntegrationUnavailable(
        "Incident analysis",
        "live evidence pipeline (orchestrator logs, event correlation, config diff) not wired yet",
    )


# ---------------------------------------------------------------- assistant
def assistant_sources(user: User) -> dict:
    s = get_settings()
    if not s.docchat_ollama_url:
        raise IntegrationUnavailable("DocChat", "DOCCHAT_OLLAMA_URL not configured")
    # No retrieval index wired yet — report the model honestly, with no corpus stats.
    return {"categories": [], "total": 0, "grounded": False, "model": s.docchat_model}


def assistant_stats(user: User) -> dict:
    return {"questions_this_month": len(_CHAT_LOG), "teams": 0}


def assistant_chat(user: User, messages: list[dict], persona: str):
    s = get_settings()
    if not s.docchat_ollama_url:
        raise IntegrationUnavailable("Ollama", "DOCCHAT_OLLAMA_URL not configured")
    try:
        import httpx
    except ImportError:
        raise IntegrationUnavailable("Ollama", "httpx not installed")

    system = (
        "You are MERIDIAN's engineering knowledge assistant, running fully on-prem for an "
        "internal engineering platform. "
        f"You are answering {user.display_name} ({user.username}), role {user.role}, "
        f"teams: {', '.join(user.teams) or '—'}. "
        f"Persona: {persona}. {_PERSONA_HINTS.get(persona, '')} "
        "Answer concisely and practically. If you are not certain something exists internally, "
        "say so rather than inventing it."
    )
    payload = {
        "model": s.docchat_model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            *[{"role": "assistant" if m.get("role") in ("assistant", "ai") else "user",
               "content": m.get("content", "")} for m in messages],
        ],
    }
    question = next((m.get("content", "") for m in reversed(messages)
                     if m.get("role") == "user"), "")
    _CHAT_LOG.append({
        "username": user.username,
        "persona": persona,
        "when": datetime.now(timezone.utc).isoformat(),
        "question": question[:300],
    })
    url = f"{s.docchat_ollama_url.rstrip('/')}/api/chat"

    def gen():
        try:
            with httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
                with client.stream("POST", url, json=payload) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        text = (chunk.get("message") or {}).get("content", "")
                        if text:
                            yield _sse("token", {"text": text})
                        if chunk.get("done"):
                            break
        except Exception as exc:  # stream already started — surface as an SSE error event
            yield _sse("error", {"detail": f"Ollama unavailable: {exc}"})
            return
        yield _sse("done", {"audit": f"served by on-prem {s.docchat_model} · "
                                     "zero data egress · exchange audited"})

    return gen()
