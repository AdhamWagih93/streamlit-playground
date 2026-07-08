"""Thin Ollama client. Falls back to deterministic text when the model
is unreachable so every feature stays demoable offline."""

import json

import requests

from ..config import settings


class OllamaUnavailable(Exception):
    pass


def available() -> bool:
    try:
        r = requests.get(f"{settings.ollama_url}/api/tags", timeout=3)
        return r.ok
    except requests.RequestException:
        return False


def chat(messages: list[dict], system: str | None = None, json_mode: bool = False) -> str:
    payload = {
        "model": settings.ollama_model,
        "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        "stream": False,
        "options": {"temperature": 0.4},
    }
    if json_mode:
        payload["format"] = "json"
    try:
        r = requests.post(f"{settings.ollama_url}/api/chat", json=payload,
                          timeout=settings.ollama_timeout)
        r.raise_for_status()
        return r.json()["message"]["content"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        raise OllamaUnavailable(str(exc)) from exc


def safe_chat(messages: list[dict], system: str | None = None, fallback: str = "") -> str:
    try:
        return chat(messages, system=system)
    except OllamaUnavailable:
        return fallback or ("(AI offline — Ollama is not reachable at "
                            f"{settings.ollama_url}. Configure OLLAMA_URL / helm "
                            "values.ai.ollamaUrl.)")


def extract_json(text: str) -> dict:
    """Best-effort JSON extraction from a model reply."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model reply")
    return json.loads(text[start:end + 1])
