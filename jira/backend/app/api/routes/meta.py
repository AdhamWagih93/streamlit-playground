"""Metadata routes: issue types, statuses, priorities, labels, custom fields."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user
from app.core.database import get_db
from app.models import CustomField, IssueType, Label, Priority, Status, User
from app.schemas.meta import (
    CustomFieldIn,
    CustomFieldOut,
    IssueTypeIn,
    IssueTypeOut,
    LabelOut,
    PriorityOut,
    StatusIn,
    StatusOut,
)

router = APIRouter()


# --- Issue types -----------------------------------------------------------
@router.get("/issue-types", response_model=list[IssueTypeOut])
def list_issue_types(
    project_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[IssueType]:
    stmt = select(IssueType)
    if project_id is not None:
        stmt = stmt.where(
            or_(IssueType.project_id.is_(None), IssueType.project_id == project_id)
        )
    else:
        stmt = stmt.where(IssueType.project_id.is_(None))
    stmt = stmt.order_by(IssueType.name)
    return list(db.scalars(stmt).all())


@router.post("/issue-types", response_model=IssueTypeOut, status_code=201)
def create_issue_type(
    payload: IssueTypeIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> IssueType:
    obj = IssueType(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# --- Statuses --------------------------------------------------------------
@router.get("/statuses", response_model=list[StatusOut])
def list_statuses(
    project_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Status]:
    stmt = select(Status)
    if project_id is not None:
        stmt = stmt.where(or_(Status.project_id.is_(None), Status.project_id == project_id))
    else:
        stmt = stmt.where(Status.project_id.is_(None))
    stmt = stmt.order_by(Status.order)
    return list(db.scalars(stmt).all())


@router.post("/statuses", response_model=StatusOut, status_code=201)
def create_status(
    payload: StatusIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> Status:
    obj = Status(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# --- Priorities ------------------------------------------------------------
@router.get("/priorities", response_model=list[PriorityOut])
def list_priorities(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Priority]:
    return list(db.scalars(select(Priority).order_by(Priority.rank)).all())


# --- Labels ----------------------------------------------------------------
@router.get("/labels", response_model=list[LabelOut])
def list_labels(
    q: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Label]:
    stmt = select(Label)
    if q:
        stmt = stmt.where(Label.name.ilike(f"%{q}%"))
    stmt = stmt.order_by(Label.name).limit(50)
    return list(db.scalars(stmt).all())


# --- Custom fields ---------------------------------------------------------
@router.get("/custom-fields", response_model=list[CustomFieldOut])
def list_custom_fields(
    project_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[CustomField]:
    stmt = select(CustomField)
    if project_id is not None:
        stmt = stmt.where(
            or_(CustomField.project_id.is_(None), CustomField.project_id == project_id)
        )
    stmt = stmt.order_by(CustomField.name)
    return list(db.scalars(stmt).all())


@router.post("/custom-fields", response_model=CustomFieldOut, status_code=201)
def create_custom_field(
    payload: CustomFieldIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> CustomField:
    obj = CustomField(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
