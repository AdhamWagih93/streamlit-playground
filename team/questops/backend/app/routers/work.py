"""Focus feed (unified priorities), Jira board actions, Jenkins signals."""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import settings
from ..db import RepoAction, User, get_db, utcnow
from ..gamification import award, quest_progress
from ..integrations import jenkins, jira

router = APIRouter(prefix="/api", tags=["work"])

PRIORITY_SCORE = {"Highest": 90, "High": 70, "Medium": 50, "Low": 35, "Lowest": 25}


def _due_bonus(due: str | None) -> tuple[int, str]:
    if not due:
        return 0, ""
    try:
        days = (dt.date.fromisoformat(due) - utcnow().date()).days
    except ValueError:
        return 0, ""
    if days < 0:
        return 35, f"overdue by {-days}d"
    if days == 0:
        return 25, "due today"
    if days <= 2:
        return 15, f"due in {days}d"
    return 0, ""


@router.get("/focus")
def focus(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """One ranked answer to 'what should I do right now?'."""
    items = []

    for issue in jira.my_open_issues(user.username):
        score = PRIORITY_SCORE.get(issue["priority"], 50)
        bonus, due_note = _due_bonus(issue.get("due"))
        score += bonus
        why = [f"{issue['priority']} priority"]
        if due_note:
            why.append(due_note)
        if issue["status"].lower() in settings.reopened_statuses:
            score += 20
            why.append("reopened — regression, needs attention")
        elif issue["status"] != settings.board_statuses[0]:
            score += 10
            why.append("already started — finish it")
        items.append({"source": "jira", "key": issue["key"], "title": issue["summary"],
                      "subtitle": f"{issue['type']} · {issue['status']}",
                      "score": score, "why": ", ".join(why), "url": issue["url"],
                      "status": issue["status"]})

    ci = jenkins.overview()
    for f in ci["failures"]:
        if f.get("claimed_by") and f["claimed_by"] != user.username:
            continue  # someone else is on it
        mine = f.get("claimed_by") == user.username
        items.append({"source": "jenkins", "key": f["job"],
                      "title": f"{f['result']}: {f['job']} #{f['number']}",
                      "subtitle": f"failed {f['ago_min']} min ago",
                      "score": 78 + (10 if mine else 0),
                      "why": "red build blocks the team" + (" — you claimed it" if mine else ""),
                      "url": f["url"], "claimed": bool(f.get("claimed_by"))})
    for l in ci["long_running"]:
        items.append({"source": "jenkins", "key": l["job"],
                      "title": f"Long-running: {l['job']} #{l['number']}",
                      "subtitle": f"running for {l['running_min']} min",
                      "score": 55, "why": "possibly stuck — check or kill it",
                      "url": l["url"], "claimed": bool(l.get("claimed_by"))})

    if user.role == "approver":
        pending = db.query(RepoAction).filter(RepoAction.status == "pending_approval").all()
        for a in pending:
            items.append({"source": "approval", "key": f"action-{a.id}",
                          "title": f"Approve: {a.title or a.template_name}",
                          "subtitle": f"requested by {a.requested_by}",
                          "score": 85, "why": "teammate blocked on your review",
                          "url": f"#actions", "action_id": a.id})

    for issue in jira.unassigned_issues()[:3]:
        score = PRIORITY_SCORE.get(issue["priority"], 50) - 20
        items.append({"source": "jira", "key": issue["key"], "title": issue["summary"],
                      "subtitle": f"{issue['type']} · unassigned", "score": max(score, 15),
                      "why": "nobody owns this yet — claim it for XP",
                      "url": issue["url"], "unassigned": True})

    items.sort(key=lambda i: -i["score"])
    return {"items": items, "quests": quest_progress(db, user.username),
            "ci_source": ci["source"]}


@router.get("/board")
def board(user: User = Depends(current_user)):
    return jira.board()


class TransitionBody(BaseModel):
    status: str


@router.post("/issues/{key}/transition")
def transition(key: str, body: TransitionBody, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    if body.status not in settings.board_statuses:
        raise HTTPException(400, f"unknown status '{body.status}'")
    try:
        issue = jira.transition_issue(key, body.status)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    st = body.status.lower()
    if st in settings.done_statuses:
        kind = "ticket_done"
    elif st in settings.review_statuses:
        kind = "ticket_resolved"
    else:
        kind = "ticket_progress"
    game = award(db, user, kind,
                 message=f"{key} → {body.status}", ref=key)
    return {"issue": issue, "game": game}


class CommentBody(BaseModel):
    body: str


@router.post("/issues/{key}/comment")
def comment(key: str, body: CommentBody, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    try:
        jira.add_comment(key, body.body, user.username)
    except KeyError:
        raise HTTPException(404, f"issue {key} not found")
    game = award(db, user, "ticket_comment", message=f"commented on {key}", ref=key)
    return {"game": game}


@router.post("/issues/{key}/claim")
def claim_issue(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        issue = jira.assign(key, user.username)
    except KeyError:
        raise HTTPException(404, f"issue {key} not found")
    game = award(db, user, "ticket_claimed", message=f"claimed {key}", ref=key)
    return {"issue": issue, "game": game}


@router.get("/ci")
def ci(user: User = Depends(current_user)):
    return jenkins.overview()


class JobBody(BaseModel):
    job: str


@router.post("/ci/claim")
def ci_claim(body: JobBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    jenkins.claim(body.job, user.username)
    game = award(db, user, "build_claimed", message=f"claimed {body.job}", ref=body.job)
    return {"game": game}


@router.post("/ci/fixed")
def ci_fixed(body: JobBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not jenkins.verify_fixed(body.job):
        raise HTTPException(409, "Jenkins still reports this job failing — XP pays out "
                            "only when the build is green")
    game = award(db, user, "build_fixed", message=f"fixed build {body.job}", ref=body.job)
    return {"game": game}
