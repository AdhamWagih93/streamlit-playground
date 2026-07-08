"""Ollama-backed copilot: contextual chat + daily briefing."""

import datetime as dt
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import settings
from ..db import RepoAction, User, get_db
from ..integrations import jenkins, jira, ollama

router = APIRouter(prefix="/api/ai", tags=["ai"])

_BRIEFING_CACHE: dict[tuple[str, str], str] = {}

CHAT_SYSTEM = (
    "You are the QuestOps copilot for a DevOps platform team. You see the user's live "
    "Jira issues, Jenkins state and pending approvals as JSON context. Be concise, "
    "concrete and action-oriented: help them decide what to do next, draft comments, "
    "or summarize state. Never invent ticket keys or job names not in the context."
)


def _context(user: User, db: Session) -> dict:
    ci = jenkins.overview()
    pending = db.query(RepoAction).filter(RepoAction.status == "pending_approval").count()
    return {
        "user": {"username": user.username, "role": user.role},
        "my_open_issues": [{k: i[k] for k in ("key", "summary", "status", "priority", "due")}
                           for i in jira.my_open_issues(user.username)],
        "unassigned_issues": [{k: i[k] for k in ("key", "summary", "priority")}
                              for i in jira.unassigned_issues()[:5]],
        "failing_builds": [{k: f[k] for k in ("job", "result", "ago_min")}
                           for f in ci["failures"]],
        "long_running_builds": [{k: l[k] for k in ("job", "running_min")}
                                for l in ci["long_running"]],
        "pending_approvals": pending,
    }


@router.get("/status")
def status(user: User = Depends(current_user)):
    return {"available": ollama.available(), "model": settings.ollama_model,
            "url": settings.ollama_url}


class ChatBody(BaseModel):
    message: str
    history: list[dict] = []  # [{role, content}]


@router.post("/chat")
def chat(body: ChatBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ctx = json.dumps(_context(user, db), default=str)
    messages = body.history[-10:] + [{"role": "user", "content": body.message}]
    reply = ollama.safe_chat(messages, system=f"{CHAT_SYSTEM}\n\nLive context:\n{ctx}")
    return {"reply": reply}


def _fallback_briefing(ctx: dict) -> str:
    """Deterministic briefing when Ollama is unreachable."""
    lines = ["**Your briefing** *(rule-based — AI offline)*"]
    if ctx["failing_builds"]:
        jobs = ", ".join(f["job"] for f in ctx["failing_builds"][:3])
        lines.append(f"- 🔴 {len(ctx['failing_builds'])} failing build(s): {jobs}")
    if ctx["long_running_builds"]:
        lines.append(f"- ⏳ {len(ctx['long_running_builds'])} long-running build(s) may be stuck")
    if ctx["my_open_issues"]:
        top = ctx["my_open_issues"][0]
        lines.append(f"- 🎯 Top ticket: {top['key']} — {top['summary']} ({top['priority']})")
        lines.append(f"- 📋 {len(ctx['my_open_issues'])} open ticket(s) assigned to you")
    if ctx["pending_approvals"] and ctx["user"]["role"] == "approver":
        lines.append(f"- 🛡️ {ctx['pending_approvals']} repo action(s) waiting for your approval")
    if len(lines) == 1:
        lines.append("- ✨ All clear. Grab an unassigned ticket from the board.")
    return "\n".join(lines)


@router.get("/briefing")
def briefing(refresh: bool = False, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    key = (user.username, dt.date.today().isoformat())
    if not refresh and key in _BRIEFING_CACHE:
        return {"briefing": _BRIEFING_CACHE[key], "cached": True}
    ctx = _context(user, db)
    text = ollama.safe_chat(
        [{"role": "user", "content":
          "Write my morning briefing: 4-6 short bullet points, most urgent first, "
          "with a one-line motivational close. Context:\n" + json.dumps(ctx, default=str)}],
        system=CHAT_SYSTEM, fallback=_fallback_briefing(ctx))
    _BRIEFING_CACHE[key] = text
    return {"briefing": text, "cached": False}
