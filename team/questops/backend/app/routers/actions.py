"""Repo actions with a hard human-approval gate:
request → AI drafts plan+files → approver reviews → execute on approve."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import current_user, require_approver
from ..db import PromptTemplate, RepoAction, User, get_db, utcnow
from ..gamification import award
from ..integrations import gitops

router = APIRouter(prefix="/api/actions", tags=["actions"])


def _payload(a: RepoAction) -> dict:
    return {"id": a.id, "title": a.title, "template_name": a.template_name,
            "repo_url": a.repo_url, "branch": a.branch, "params": a.params,
            "plan": a.plan, "files": a.files, "commit_message": a.commit_message,
            "status": a.status, "requested_by": a.requested_by,
            "decided_by": a.decided_by, "decision_note": a.decision_note,
            "result": a.result, "created_at": a.created_at.isoformat()}


@router.get("")
def list_actions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    actions = db.query(RepoAction).order_by(RepoAction.created_at.desc()).limit(100).all()
    return {"actions": [_payload(a) for a in actions],
            "can_approve": user.role == "approver"}


class CreateActionBody(BaseModel):
    template_id: int
    repo_url: str
    branch: str = ""
    title: str = ""
    params: dict = {}


@router.post("")
def create_action(body: CreateActionBody, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    template = db.get(PromptTemplate, body.template_id)
    if template is None:
        raise HTTPException(404, "template not found")
    draft = gitops.generate_plan(template, body.params, body.repo_url, body.branch)
    action = RepoAction(
        title=body.title or template.name, template_id=template.id,
        template_name=template.name, repo_url=body.repo_url, branch=body.branch,
        params=body.params, plan=draft["plan"], files=draft["files"],
        commit_message=draft["commit_message"], status="pending_approval",
        requested_by=user.username)
    db.add(action)
    db.commit()
    game = award(db, user, "repo_action_requested",
                 message=f"requested repo action '{action.title}'", ref=f"action-{action.id}")
    return {"action": _payload(action), "game": game}


class DecisionBody(BaseModel):
    note: str = ""


@router.post("/{action_id}/approve")
def approve(action_id: int, body: DecisionBody, user: User = Depends(require_approver),
            db: Session = Depends(get_db)):
    action = db.get(RepoAction, action_id)
    if action is None:
        raise HTTPException(404, "action not found")
    if action.status != "pending_approval":
        raise HTTPException(409, f"action is '{action.status}', not pending")

    action.status = "approved"
    action.decided_by = user.username
    action.decision_note = body.note
    action.decided_at = utcnow()
    db.commit()

    try:
        action.result = gitops.execute(action)
        action.status = "executed"
    except Exception as exc:  # noqa: BLE001 — surface any git failure to the UI
        action.result = str(exc)
        action.status = "failed"
    action.executed_at = utcnow()
    db.commit()

    game = award(db, user, "approval_review",
                 message=f"approved '{action.title}' by {action.requested_by}",
                 ref=f"action-{action.id}")
    if action.status == "executed":
        requester = db.get(User, action.requested_by)
        if requester is not None:
            award(db, requester, "repo_action_executed",
                  message=f"repo action '{action.title}' landed", ref=f"action-{action.id}")
    return {"action": _payload(action), "game": game}


@router.post("/{action_id}/reject")
def reject(action_id: int, body: DecisionBody, user: User = Depends(require_approver),
           db: Session = Depends(get_db)):
    action = db.get(RepoAction, action_id)
    if action is None:
        raise HTTPException(404, "action not found")
    if action.status != "pending_approval":
        raise HTTPException(409, f"action is '{action.status}', not pending")
    action.status = "rejected"
    action.decided_by = user.username
    action.decision_note = body.note
    action.decided_at = utcnow()
    db.commit()
    game = award(db, user, "approval_review",
                 message=f"rejected '{action.title}'", ref=f"action-{action.id}")
    return {"action": _payload(action), "game": game}
