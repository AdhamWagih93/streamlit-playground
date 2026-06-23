"""Search routes: TQL execution, validation, and saved filters."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Issue, SavedFilter, User
from app.schemas.common import Message, Page
from app.schemas.issue import IssueListItem
from app.services.serializers import to_list_item
from app.services.tql import TQLError, build_query

router = APIRouter()


# --- Search bodies ---------------------------------------------------------
class SearchRequest(BaseModel):
    tql: str = ""
    page: int = 1
    page_size: int = 50


def _run_search(db: Session, tql: str, page: int, page_size: int, user_id: int) -> Page[IssueListItem]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    try:
        where, order_by = build_query(db, tql, user_id)
    except TQLError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    stmt = select(Issue)
    if where is not None:
        stmt = stmt.where(where)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(*order_by).offset((page - 1) * page_size).limit(page_size)
    items = [to_list_item(i) for i in db.scalars(stmt)]
    return Page[IssueListItem](items=items, total=total, page=page, page_size=page_size)


@router.get("/search", response_model=Page[IssueListItem])
def search_get(
    tql: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[IssueListItem]:
    return _run_search(db, tql, page, page_size, user.id)


@router.post("", response_model=Page[IssueListItem])
def search_post(
    payload: SearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[IssueListItem]:
    return _run_search(db, payload.tql, payload.page, payload.page_size, user.id)


# --- Validation ------------------------------------------------------------
class ValidationResult(BaseModel):
    valid: bool
    error: str | None = None


@router.get("/validate", response_model=ValidationResult)
def validate_tql(
    tql: str = Query(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ValidationResult:
    try:
        build_query(db, tql, user.id)
    except TQLError as exc:
        return ValidationResult(valid=False, error=str(exc))
    return ValidationResult(valid=True, error=None)


# --- Saved filters ---------------------------------------------------------
class SavedFilterOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    query: str
    is_shared: bool
    owner_id: int


class SavedFilterIn(BaseModel):
    name: str
    query: str
    is_shared: bool = False


class SavedFilterUpdate(BaseModel):
    name: str | None = None
    query: str | None = None
    is_shared: bool | None = None


@router.get("/filters", response_model=list[SavedFilterOut])
def list_filters(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SavedFilter]:
    return list(
        db.scalars(
            select(SavedFilter)
            .where(
                or_(
                    SavedFilter.owner_id == user.id,
                    SavedFilter.is_shared.is_(True),
                )
            )
            .order_by(SavedFilter.name.asc())
        )
    )


@router.post("/filters", response_model=SavedFilterOut, status_code=status.HTTP_201_CREATED)
def create_filter(
    payload: SavedFilterIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SavedFilter:
    saved = SavedFilter(
        owner_id=user.id,
        name=payload.name,
        query=payload.query,
        is_shared=payload.is_shared,
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return saved


@router.patch("/filters/{fid}", response_model=SavedFilterOut)
def update_filter(
    fid: int,
    payload: SavedFilterUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SavedFilter:
    saved = db.get(SavedFilter, fid)
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found"
        )
    if saved.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner may edit this filter"
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(saved, field, value)
    db.commit()
    db.refresh(saved)
    return saved


@router.delete("/filters/{fid}", response_model=Message)
def delete_filter(
    fid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    saved = db.get(SavedFilter, fid)
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found"
        )
    if saved.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner may delete this filter"
        )
    db.delete(saved)
    db.commit()
    return Message(detail="Filter deleted")
