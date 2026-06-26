"""Issue routes: CRUD plus comments, worklogs, history, links, attachments and ranking."""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models import (
    Attachment,
    Comment,
    Component,
    Issue,
    IssueHistory,
    IssueLink,
    Project,
    Status,
    User,
    Version,
    Worklog,
)
from app.schemas.common import Message, Page
from app.schemas.issue import (
    AttachmentOut,
    CommentIn,
    CommentOut,
    HistoryOut,
    IssueCreate,
    IssueDetail,
    IssueLinkIn,
    IssueLinkOut,
    IssueListItem,
    IssueRankUpdate,
    IssueUpdate,
    WorklogIn,
    WorklogOut,
)
from app.services.issues import (
    _now,
    allocate_key,
    apply_update,
    bottom_rank,
    default_status_id,
    notify,
    record_history,
    resolve_labels,
)
from app.services import permission_keys as P
from app.services.permissions import (
    assert_own_or_all,
    assert_project_permission,
    visible_project_ids,
)
from app.services.serializers import issue_ref, to_detail, to_list_item
from app.utils.ranking import rank_between
from app.utils.timetracking import parse_duration

router = APIRouter()


def _resolve_issue(db: Session, key_or_id: str | int) -> Issue:
    """Return an Issue by integer id or by key like 'ENG-42'. 404 if missing."""
    issue: Issue | None = None
    key_str = str(key_or_id)
    if key_str.isdigit():
        issue = db.get(Issue, int(key_str))
    if issue is None:
        issue = db.scalars(
            select(Issue).where(Issue.key == key_str.upper())
        ).first()
    if issue is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found"
        )
    return issue


# --- Issue CRUD ------------------------------------------------------------
@router.post("", response_model=IssueDetail, status_code=status.HTTP_201_CREATED)
def create_issue(
    payload: IssueCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> IssueDetail:
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    assert_project_permission(db, user, project, P.CREATE_ISSUES)

    key, number = allocate_key(db, project)
    status_id = payload.status_id or default_status_id(db, project.id)
    rank = bottom_rank(db, project.id)
    reporter_id = payload.reporter_id or user.id

    issue = Issue(
        key=key,
        number=number,
        project_id=project.id,
        type_id=payload.type_id,
        status_id=status_id,
        priority_id=payload.priority_id,
        summary=payload.summary,
        description=payload.description,
        reporter_id=reporter_id,
        assignee_id=payload.assignee_id,
        parent_id=payload.parent_id,
        epic_id=payload.epic_id,
        sprint_id=payload.sprint_id,
        story_points=payload.story_points,
        due_date=payload.due_date,
        rank=rank,
    )
    issue.labels = resolve_labels(db, payload.label_names)
    if payload.component_ids:
        issue.components = list(
            db.scalars(select(Component).where(Component.id.in_(payload.component_ids)))
        )
    if payload.fix_version_ids:
        issue.fix_versions = list(
            db.scalars(select(Version).where(Version.id.in_(payload.fix_version_ids)))
        )

    db.add(issue)
    db.flush()

    record_history(db, issue, user.id, "created", None, issue.key)
    if issue.assignee_id:
        notify(db, issue.assignee_id, user.id, issue, "assigned",
               f"{issue.key} was assigned to you")

    db.commit()
    db.refresh(issue)
    return to_detail(issue)


@router.get("", response_model=Page[IssueListItem])
def list_issues(
    project: str | None = Query(None),
    status_id: int | None = Query(None),
    assignee_id: int | None = Query(None),
    type_id: int | None = Query(None),
    sprint_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[IssueListItem]:
    stmt = select(Issue)
    if project is not None:
        proj_str = str(project)
        if proj_str.isdigit():
            stmt = stmt.where(Issue.project_id == int(proj_str))
        else:
            proj = db.scalars(
                select(Project).where(Project.key == proj_str.upper())
            ).first()
            stmt = stmt.where(Issue.project_id == (proj.id if proj else -1))
    if status_id is not None:
        stmt = stmt.where(Issue.status_id == status_id)
    if assignee_id is not None:
        stmt = stmt.where(Issue.assignee_id == assignee_id)
    if type_id is not None:
        stmt = stmt.where(Issue.type_id == type_id)
    if sprint_id is not None:
        stmt = stmt.where(Issue.sprint_id == sprint_id)

    vis = visible_project_ids(db, user)
    if vis is not None:
        stmt = stmt.where(Issue.project_id.in_(vis or {-1}))

    total = db.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0
    stmt = stmt.order_by(Issue.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = [to_list_item(i) for i in db.scalars(stmt)]
    return Page[IssueListItem](items=items, total=total, page=page, page_size=page_size)


@router.get("/{key_or_id}", response_model=IssueDetail)
def get_issue(
    key_or_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> IssueDetail:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.BROWSE_PROJECTS, issue=issue)
    return to_detail(issue)


@router.patch("/{key_or_id}", response_model=IssueDetail)
def update_issue(
    key_or_id: str,
    payload: IssueUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> IssueDetail:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.EDIT_ISSUES, issue=issue)
    data = payload.model_dump(exclude_unset=True)
    if "status_id" in data:
        assert_project_permission(db, user, issue.project, P.TRANSITION_ISSUES, issue=issue)
    if "assignee_id" in data:
        assert_project_permission(db, user, issue.project, P.ASSIGN_ISSUES, issue=issue)
    apply_update(db, issue, data, user.id)
    db.commit()
    db.refresh(issue)
    return to_detail(issue)


@router.delete("/{key_or_id}", response_model=Message)
def delete_issue(
    key_or_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.DELETE_ISSUES, issue=issue)
    db.delete(issue)
    db.commit()
    return Message(detail="Issue deleted")


# --- Comments --------------------------------------------------------------
@router.get("/{key_or_id}/comments", response_model=list[CommentOut])
def list_comments(
    key_or_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Comment]:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.BROWSE_PROJECTS, issue=issue)
    return list(issue.comments)


@router.post(
    "/{key_or_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    key_or_id: str,
    payload: CommentIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Comment:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.ADD_COMMENTS, issue=issue)
    comment = Comment(issue_id=issue.id, author_id=user.id, body=payload.body)
    db.add(comment)
    db.flush()
    message = f"{user.display_name} commented on {issue.key}"
    notify(db, issue.assignee_id, user.id, issue, "commented", message)
    notify(db, issue.reporter_id, user.id, issue, "commented", message)
    db.commit()
    db.refresh(comment)
    return comment


@router.patch("/{key_or_id}/comments/{cid}", response_model=CommentOut)
def update_comment(
    key_or_id: str,
    cid: int,
    payload: CommentIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Comment:
    issue = _resolve_issue(db, key_or_id)
    comment = db.scalars(
        select(Comment).where(Comment.id == cid, Comment.issue_id == issue.id)
    ).first()
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found"
        )
    assert_own_or_all(
        db, user, issue.project, comment.author_id,
        P.EDIT_OWN_COMMENTS, P.EDIT_ALL_COMMENTS, issue=issue,
    )
    comment.body = payload.body
    db.commit()
    db.refresh(comment)
    return comment


@router.delete("/{key_or_id}/comments/{cid}", response_model=Message)
def delete_comment(
    key_or_id: str,
    cid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    issue = _resolve_issue(db, key_or_id)
    comment = db.scalars(
        select(Comment).where(Comment.id == cid, Comment.issue_id == issue.id)
    ).first()
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found"
        )
    assert_own_or_all(
        db, user, issue.project, comment.author_id,
        P.DELETE_OWN_COMMENTS, P.DELETE_ALL_COMMENTS, issue=issue,
    )
    db.delete(comment)
    db.commit()
    return Message(detail="Comment deleted")


# --- Worklogs --------------------------------------------------------------
@router.get("/{key_or_id}/worklogs", response_model=list[WorklogOut])
def list_worklogs(
    key_or_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Worklog]:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.BROWSE_PROJECTS, issue=issue)
    return list(issue.worklogs)


@router.post(
    "/{key_or_id}/worklogs",
    response_model=WorklogOut,
    status_code=status.HTTP_201_CREATED,
)
def create_worklog(
    key_or_id: str,
    payload: WorklogIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Worklog:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.WORK_ON_ISSUES, issue=issue)
    seconds = payload.time_spent_seconds or parse_duration(payload.time_spent)
    if not seconds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid time_spent or time_spent_seconds is required",
        )
    worklog = Worklog(
        issue_id=issue.id,
        author_id=user.id,
        time_spent_seconds=seconds,
        comment=payload.comment,
        started_at=payload.started_at or _now(),
    )
    db.add(worklog)
    if issue.remaining_estimate_seconds is not None:
        issue.remaining_estimate_seconds = max(0, issue.remaining_estimate_seconds - seconds)
    db.commit()
    db.refresh(worklog)
    return worklog


@router.delete("/{key_or_id}/worklogs/{wid}", response_model=Message)
def delete_worklog(
    key_or_id: str,
    wid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    issue = _resolve_issue(db, key_or_id)
    worklog = db.scalars(
        select(Worklog).where(Worklog.id == wid, Worklog.issue_id == issue.id)
    ).first()
    if worklog is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Worklog not found"
        )
    assert_own_or_all(
        db, user, issue.project, worklog.author_id,
        P.DELETE_OWN_WORKLOGS, P.DELETE_ALL_WORKLOGS, issue=issue,
    )
    db.delete(worklog)
    db.commit()
    return Message(detail="Worklog deleted")


# --- History ---------------------------------------------------------------
@router.get("/{key_or_id}/history", response_model=list[HistoryOut])
def list_history(
    key_or_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[IssueHistory]:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.BROWSE_PROJECTS, issue=issue)
    return list(
        db.scalars(
            select(IssueHistory)
            .where(IssueHistory.issue_id == issue.id)
            .order_by(IssueHistory.created_at.asc())
        )
    )


# --- Links -----------------------------------------------------------------
@router.post(
    "/{key_or_id}/links",
    response_model=IssueLinkOut,
    status_code=status.HTTP_201_CREATED,
)
def create_link(
    key_or_id: str,
    payload: IssueLinkIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> IssueLinkOut:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.LINK_ISSUES, issue=issue)
    target = db.scalars(
        select(Issue).where(Issue.key == payload.target_key.upper())
    ).first()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Target issue not found"
        )
    if target.id == issue.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="An issue cannot link to itself"
        )
    existing = db.scalars(
        select(IssueLink).where(
            IssueLink.source_id == issue.id,
            IssueLink.target_id == target.id,
            IssueLink.link_type == payload.link_type,
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This link already exists"
        )
    link = IssueLink(source_id=issue.id, target_id=target.id, link_type=payload.link_type)
    db.add(link)
    db.commit()
    db.refresh(link)
    return IssueLinkOut(id=link.id, link_type=link.link_type, issue=issue_ref(target))


@router.delete("/{key_or_id}/links/{link_id}", response_model=Message)
def delete_link(
    key_or_id: str,
    link_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.LINK_ISSUES, issue=issue)
    link = db.scalars(
        select(IssueLink).where(
            IssueLink.id == link_id,
            (IssueLink.source_id == issue.id) | (IssueLink.target_id == issue.id),
        )
    ).first()
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Link not found"
        )
    db.delete(link)
    db.commit()
    return Message(detail="Link deleted")


# --- Attachments -----------------------------------------------------------
@router.post(
    "/{key_or_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    key_or_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Attachment:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.CREATE_ATTACHMENTS, issue=issue)
    data = await file.read()
    max_bytes = settings.max_attachment_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_attachment_mb} MB limit",
        )
    os.makedirs(settings.attachments_dir, exist_ok=True)
    storage_key = uuid.uuid4().hex
    path = os.path.join(settings.attachments_dir, storage_key)
    with open(path, "wb") as fh:
        fh.write(data)

    attachment = Attachment(
        issue_id=issue.id,
        author_id=user.id,
        filename=file.filename or storage_key,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        storage_key=storage_key,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


@router.get("/{key_or_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(
    key_or_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Attachment]:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.BROWSE_PROJECTS, issue=issue)
    return list(issue.attachments)


@router.get("/attachments/{aid}/download")
def download_attachment(
    aid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    attachment = db.get(Attachment, aid)
    if attachment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found"
        )
    issue = _resolve_issue(db, attachment.issue_id)
    assert_project_permission(db, user, issue.project, P.BROWSE_PROJECTS, issue=issue)
    path = os.path.join(settings.attachments_dir, attachment.storage_key)
    if not os.path.exists(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attachment file missing"
        )
    return FileResponse(
        path,
        filename=attachment.filename,
        media_type=attachment.content_type,
    )


@router.delete("/attachments/{aid}", response_model=Message)
def delete_attachment(
    aid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    attachment = db.get(Attachment, aid)
    if attachment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found"
        )
    issue = _resolve_issue(db, attachment.issue_id)
    assert_own_or_all(
        db, user, issue.project, attachment.author_id,
        P.DELETE_OWN_ATTACHMENTS, P.DELETE_ALL_ATTACHMENTS, issue=issue,
    )
    path = os.path.join(settings.attachments_dir, attachment.storage_key)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
    db.delete(attachment)
    db.commit()
    return Message(detail="Attachment deleted")


# --- Rank / move -----------------------------------------------------------
@router.put("/{key_or_id}/rank", response_model=IssueListItem)
def rank_issue(
    key_or_id: str,
    payload: IssueRankUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> IssueListItem:
    issue = _resolve_issue(db, key_or_id)
    assert_project_permission(db, user, issue.project, P.EDIT_ISSUES, issue=issue)
    if payload.sprint_id is not None:
        assert_project_permission(db, user, issue.project, P.MANAGE_SPRINTS, issue=issue)

    low: str | None = None
    high: str | None = None
    if payload.after_id is not None:
        after = db.get(Issue, payload.after_id)
        if after is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="after_id issue not found"
            )
        low = after.rank
    if payload.before_id is not None:
        before = db.get(Issue, payload.before_id)
        if before is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="before_id issue not found"
            )
        high = before.rank

    if payload.after_id is not None or payload.before_id is not None:
        issue.rank = rank_between(low, high)
    elif payload.sprint_id is not None or payload.status_id is not None:
        # No neighbours given but a sprint/status move requested: send to bottom.
        issue.rank = bottom_rank(db, issue.project_id)

    if payload.sprint_id is not None:
        issue.sprint_id = payload.sprint_id
    if payload.status_id is not None and payload.status_id != issue.status_id:
        old_status = db.get(Status, issue.status_id)
        new_status = db.get(Status, payload.status_id)
        record_history(
            db, issue, user.id, "status",
            old_status.name if old_status else None,
            new_status.name if new_status else None,
        )
        issue.status_id = payload.status_id

    db.commit()
    db.refresh(issue)
    return to_list_item(issue)
