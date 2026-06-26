"""Agile routes: boards, the active-board view, backlog and sprints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user
from app.api.routes.projects import _resolve_project
from app.core.database import get_db
from app.models import Board, Issue, Project, Sprint, Status, User
from app.services import permission_keys as P
from app.services.permissions import assert_project_permission
from app.schemas.agile import (
    BacklogView,
    BoardColumn,
    BoardIn,
    BoardOut,
    BoardView,
    SprintIn,
    SprintOut,
    SprintUpdate,
)
from app.schemas.common import Message
from app.services.serializers import to_list_item

router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_board(db: Session, board_id: int) -> Board:
    board = db.get(Board, board_id)
    if board is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Board not found"
        )
    return board


def _get_sprint(db: Session, sprint_id: int) -> Sprint:
    sprint = db.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sprint not found"
        )
    return sprint


def _board_project(db: Session, board: Board) -> Project:
    return db.get(Project, board.project_id)


def _sprint_project(db: Session, sprint: Sprint) -> Project:
    board = db.get(Board, sprint.board_id)
    return db.get(Project, board.project_id) if board else None


def _project_statuses(db: Session, project_id: int) -> list[Status]:
    """Global (project_id is null) + project-specific statuses, ordered."""
    return list(
        db.scalars(
            select(Status)
            .where(
                (Status.project_id == project_id) | (Status.project_id.is_(None))
            )
            .order_by(Status.order.asc())
        )
    )


# --- Boards ----------------------------------------------------------------
@router.get("/boards", response_model=list[BoardOut])
def list_boards(
    project_id: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Board]:
    project = _resolve_project(db, project_id)
    assert_project_permission(db, user, project, P.BROWSE_PROJECTS)
    return list(
        db.scalars(select(Board).where(Board.project_id == project.id))
    )


@router.post("/boards", response_model=BoardOut, status_code=status.HTTP_201_CREATED)
def create_board(
    payload: BoardIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Board:
    project = _resolve_project(db, payload.project_id)
    assert_project_permission(db, admin, project, P.ADMINISTER_PROJECTS)
    board = Board(
        project_id=project.id,
        name=payload.name,
        board_type=payload.board_type,
    )
    db.add(board)
    db.commit()
    db.refresh(board)
    return board


@router.get("/boards/{board_id}", response_model=BoardOut)
def get_board(
    board_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Board:
    board = _get_board(db, board_id)
    assert_project_permission(db, user, _board_project(db, board), P.BROWSE_PROJECTS)
    return board


@router.get("/boards/{board_id}/board", response_model=BoardView)
def board_view(
    board_id: int,
    sprint_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BoardView:
    board = _get_board(db, board_id)
    assert_project_permission(db, user, _board_project(db, board), P.BROWSE_PROJECTS)
    statuses = _project_statuses(db, board.project_id)

    active_sprint: Sprint | None = None
    stmt = select(Issue).where(Issue.project_id == board.project_id)

    if board.board_type == "scrum":
        if sprint_id is not None:
            active_sprint = db.get(Sprint, sprint_id)
        else:
            active_sprint = db.scalars(
                select(Sprint).where(
                    Sprint.board_id == board.id, Sprint.state == "active"
                )
            ).first()
        # Only issues in the selected/active sprint populate a scrum board.
        target_sprint_id = active_sprint.id if active_sprint else None
        stmt = stmt.where(Issue.sprint_id == target_sprint_id)
    # Kanban: all issues in the project, no sprint filter (done issues live in
    # the done column). No extra filtering required.

    stmt = stmt.order_by(Issue.rank.asc())
    issues = list(db.scalars(stmt))

    by_status: dict[int, list[Issue]] = {}
    for issue in issues:
        by_status.setdefault(issue.status_id, []).append(issue)

    columns = [
        BoardColumn(
            status_id=s.id,
            status_name=s.name,
            category=s.category,
            issues=[to_list_item(i) for i in by_status.get(s.id, [])],
        )
        for s in statuses
    ]

    return BoardView(
        board=BoardOut.model_validate(board),
        columns=columns,
        active_sprint=(
            SprintOut.model_validate(active_sprint) if active_sprint else None
        ),
    )


@router.get("/boards/{board_id}/backlog", response_model=BacklogView)
def backlog_view(
    board_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BacklogView:
    board = _get_board(db, board_id)
    assert_project_permission(db, user, _board_project(db, board), P.BROWSE_PROJECTS)

    # Future + active sprints for this board (non-closed).
    sprints = list(
        db.scalars(
            select(Sprint)
            .where(Sprint.board_id == board.id, Sprint.state != "closed")
            .order_by(Sprint.id.asc())
        )
    )

    sprint_issues: dict[int, list] = {}
    for sprint in sprints:
        items = list(
            db.scalars(
                select(Issue)
                .where(Issue.sprint_id == sprint.id)
                .order_by(Issue.rank.asc())
            )
        )
        sprint_issues[sprint.id] = [to_list_item(i) for i in items]

    # Backlog: issues in the project with no sprint and not in a done status.
    done_status_ids = [
        s.id
        for s in _project_statuses(db, board.project_id)
        if s.category == "done"
    ]
    backlog_stmt = (
        select(Issue)
        .where(
            Issue.project_id == board.project_id,
            Issue.sprint_id.is_(None),
        )
        .order_by(Issue.rank.asc())
    )
    if done_status_ids:
        backlog_stmt = backlog_stmt.where(Issue.status_id.notin_(done_status_ids))
    backlog = [to_list_item(i) for i in db.scalars(backlog_stmt)]

    return BacklogView(
        board=BoardOut.model_validate(board),
        sprints=[SprintOut.model_validate(s) for s in sprints],
        sprint_issues=sprint_issues,
        backlog=backlog,
    )


# --- Sprints ---------------------------------------------------------------
@router.post(
    "/boards/{board_id}/sprints",
    response_model=SprintOut,
    status_code=status.HTTP_201_CREATED,
)
def create_sprint(
    board_id: int,
    payload: SprintIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Sprint:
    board = _get_board(db, board_id)
    assert_project_permission(db, user, _board_project(db, board), P.MANAGE_SPRINTS)
    sprint = Sprint(
        board_id=board.id,
        name=payload.name,
        goal=payload.goal,
        state="future",
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    db.add(sprint)
    db.commit()
    db.refresh(sprint)
    return sprint


@router.get("/sprints/{sprint_id}", response_model=SprintOut)
def get_sprint(
    sprint_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Sprint:
    sprint = _get_sprint(db, sprint_id)
    assert_project_permission(db, user, _sprint_project(db, sprint), P.BROWSE_PROJECTS)
    return sprint


@router.patch("/sprints/{sprint_id}", response_model=SprintOut)
def update_sprint(
    sprint_id: int,
    payload: SprintUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Sprint:
    sprint = _get_sprint(db, sprint_id)
    assert_project_permission(db, user, _sprint_project(db, sprint), P.MANAGE_SPRINTS)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(sprint, field, value)
    db.add(sprint)
    db.commit()
    db.refresh(sprint)
    return sprint


@router.post("/sprints/{sprint_id}/start", response_model=SprintOut)
def start_sprint(
    sprint_id: int,
    payload: SprintUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Sprint:
    sprint = _get_sprint(db, sprint_id)
    assert_project_permission(db, user, _sprint_project(db, sprint), P.MANAGE_SPRINTS)
    other_active = db.scalars(
        select(Sprint).where(
            Sprint.board_id == sprint.board_id,
            Sprint.state == "active",
            Sprint.id != sprint.id,
        )
    ).first()
    if other_active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another sprint is already active on this board",
        )
    sprint.state = "active"
    if sprint.start_date is None:
        sprint.start_date = _now()
    if payload.end_date is not None:
        sprint.end_date = payload.end_date
    db.add(sprint)
    db.commit()
    db.refresh(sprint)
    return sprint


@router.post("/sprints/{sprint_id}/complete", response_model=SprintOut)
def complete_sprint(
    sprint_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Sprint:
    sprint = _get_sprint(db, sprint_id)
    assert_project_permission(db, user, _sprint_project(db, sprint), P.MANAGE_SPRINTS)
    sprint.state = "closed"
    sprint.complete_date = _now()

    # Move incomplete issues (not in a done status) back to the backlog.
    done_status_ids = [
        s.id
        for s in db.scalars(select(Status))
        if s.category == "done"
    ]
    issues = list(
        db.scalars(select(Issue).where(Issue.sprint_id == sprint.id))
    )
    for issue in issues:
        if issue.status_id not in done_status_ids:
            issue.sprint_id = None
            db.add(issue)

    db.add(sprint)
    db.commit()
    db.refresh(sprint)
    return sprint


@router.delete("/sprints/{sprint_id}", response_model=Message)
def delete_sprint(
    sprint_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    sprint = _get_sprint(db, sprint_id)
    assert_project_permission(db, user, _sprint_project(db, sprint), P.MANAGE_SPRINTS)
    # Move its issues back to the backlog before deletion.
    issues = list(
        db.scalars(select(Issue).where(Issue.sprint_id == sprint.id))
    )
    for issue in issues:
        issue.sprint_id = None
        db.add(issue)
    db.flush()
    db.delete(sprint)
    db.commit()
    return Message(detail="Sprint deleted")
