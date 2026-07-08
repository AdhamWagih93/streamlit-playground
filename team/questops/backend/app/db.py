import datetime as dt

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(120), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default="member")  # member | approver
    xp: Mapped[int] = mapped_column(Integer, default=0)
    streak: Mapped[int] = mapped_column(Integer, default=0)
    last_active: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class XPEvent(Base):
    """Single source of truth for gamification + activity history."""

    __tablename__ = "xp_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(400), default="")
    ref: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


class BadgeAward(Base):
    __tablename__ = "badge_awards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(120), index=True)
    key: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    icon: Mapped[str] = mapped_column(String(10), default="🏅")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(String(400), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    variables: Mapped[list] = mapped_column(JSON, default=list)  # ["service_name", ...]
    updated_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RepoAction(Base):
    """AI-generated repo change; executes ONLY after human approval."""

    __tablename__ = "repo_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    template_id: Mapped[int] = mapped_column(Integer, default=0)
    template_name: Mapped[str] = mapped_column(String(200), default="")
    repo_url: Mapped[str] = mapped_column(String(500), default="")
    branch: Mapped[str] = mapped_column(String(200), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    plan: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[list] = mapped_column(JSON, default=list)  # [{path, content}]
    commit_message: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(30), default="pending_approval", index=True)
    # pending_approval | approved | rejected | executed | failed
    requested_by: Mapped[str] = mapped_column(String(120), default="")
    decided_by: Mapped[str] = mapped_column(String(120), default="")
    decision_note: Mapped[str] = mapped_column(String(400), default="")
    result: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


_engine_kwargs = {}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, pool_pre_ping=True, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
