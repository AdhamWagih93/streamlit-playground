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


class Repository(Base):
    """Repositories-page entries — defined from the UI, cloned with the
    shared ADO credentials from config."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    added_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class GiteaTarget(Base):
    """A self-hosted Gitea instance that receives ONE ADO collection during the
    ADO->Gitea migration. Configured from the Access page (one instance per
    collection). The token is stored to talk to the Gitea API and is never
    returned to the UI (masked)."""

    __tablename__ = "gitea_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection: Mapped[str] = mapped_column(String(200), unique=True)  # ADO collection
    url: Mapped[str] = mapped_column(String(500))                      # https://gitea.host
    token: Mapped[str] = mapped_column(String(200), default="")        # API token
    # how an ADO project maps to a Gitea org name:
    #   "project"            -> <project>
    #   "collection_project" -> <collection>-<project>
    org_strategy: Mapped[str] = mapped_column(String(40), default="project")
    added_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class DevopsProjectsAudit(Base):
    """Permanent audit log of every write to the platform DB's devops_projects
    table (insert / update / delete / dedupe) made through the Access page."""

    __tablename__ = "devops_projects_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, index=True)
    username: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(20), index=True)   # insert/update/delete/dedupe
    project: Mapped[str] = mapped_column(String(200), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)     # fields written, etc.
    affected: Mapped[int] = mapped_column(Integer, default=0)     # rows touched


class ActivityEvent(Base):
    """High-level QuestOps usage: logins, page views, notable actions —
    one row per event, the source for the Activity page."""

    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)   # login | page | action
    page: Mapped[str] = mapped_column(String(60), default="")
    detail: Mapped[str] = mapped_column(String(300), default="")


class AgentCommand(Base):
    """Every command/action the repo agent proposes. Nothing executes until a
    human approves; the row is the permanent audit log either way."""

    __tablename__ = "agent_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)
    repo_slot: Mapped[int] = mapped_column(Integer, index=True)
    repo_name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str] = mapped_column(String(120), index=True)  # who ran the chat
    tool: Mapped[str] = mapped_column(String(60))
    input: Mapped[str] = mapped_column(Text, default="")            # JSON args
    write: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending | executed | denied | error
    output: Mapped[str] = mapped_column(Text, default="")
    requested_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    decided_by: Mapped[str] = mapped_column(String(120), default="")
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


_engine_kwargs = {}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, pool_pre_ping=True, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(retries: int = 30, delay: float = 1.0) -> None:
    """Retry while the database container is still coming up — compose
    orchestrators (podman-compose especially) don't gate on healthchecks."""
    import time

    from sqlalchemy.exc import OperationalError

    for attempt in range(retries):
        try:
            Base.metadata.create_all(engine)
            return
        except OperationalError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
