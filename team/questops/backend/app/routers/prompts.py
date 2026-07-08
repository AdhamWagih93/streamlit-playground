"""Prompt templates: visible, editable, and refinable via AI.
AI refinement returns a PROPOSAL — a human saves it explicitly."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import PromptTemplate, User, get_db
from ..gamification import award
from ..integrations import ollama
from ..integrations.gitops import extract_variables

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

REFINE_SYSTEM = (
    "You improve prompt templates used to drive automated changes to git repositories. "
    "Keep {{variable}} placeholders intact (you may add new ones). Make the template "
    "clearer, safer and more specific. Reply ONLY with the improved template text, "
    "no preamble."
)


def _payload(t: PromptTemplate) -> dict:
    return {"id": t.id, "name": t.name, "description": t.description, "body": t.body,
            "variables": t.variables, "updated_by": t.updated_by,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None}


@router.get("")
def list_templates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"templates": [_payload(t) for t in
                          db.query(PromptTemplate).order_by(PromptTemplate.name).all()]}


class TemplateBody(BaseModel):
    name: str
    description: str = ""
    body: str


@router.post("")
def create_template(body: TemplateBody, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    if db.query(PromptTemplate).filter(PromptTemplate.name == body.name).first():
        raise HTTPException(409, f"template '{body.name}' already exists")
    t = PromptTemplate(name=body.name, description=body.description, body=body.body,
                       variables=extract_variables(body.body), updated_by=user.username)
    db.add(t)
    db.commit()
    game = award(db, user, "prompt_created", message=f"created template '{t.name}'",
                 ref=f"prompt-{t.id}")
    return {"template": _payload(t), "game": game}


@router.put("/{template_id}")
def update_template(template_id: int, body: TemplateBody,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(PromptTemplate, template_id)
    if t is None:
        raise HTTPException(404, "template not found")
    t.name, t.description, t.body = body.name, body.description, body.body
    t.variables = extract_variables(body.body)
    t.updated_by = user.username
    db.commit()
    return {"template": _payload(t)}


@router.delete("/{template_id}")
def delete_template(template_id: int, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    t = db.get(PromptTemplate, template_id)
    if t is None:
        raise HTTPException(404, "template not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


class RefineBody(BaseModel):
    instruction: str = ""


@router.post("/{template_id}/refine")
def refine_template(template_id: int, body: RefineBody,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(PromptTemplate, template_id)
    if t is None:
        raise HTTPException(404, "template not found")
    ask = f"Template:\n{t.body}"
    if body.instruction:
        ask += f"\n\nSpecific request from the user: {body.instruction}"
    proposal = ollama.safe_chat([{"role": "user", "content": ask}], system=REFINE_SYSTEM)
    game = award(db, user, "prompt_refined", message=f"AI-refined '{t.name}'",
                 ref=f"prompt-{t.id}")
    return {"proposal": proposal.strip(), "game": game}
