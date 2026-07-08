"""Event Log slice — role-scoped, filterable, paginated view over world.events."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...auth.rbac import User
from .scope import visible_app_names
from .world import get_world

WINDOWS: dict[str, timedelta | None] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "1y": timedelta(days=365),
    "all": None,
}

# Presentation metadata for the type pills — order is the display order.
TYPE_META = [
    {"type": "build-develop", "label": "BUILD·DEV", "color": "blue"},
    {"type": "build-release", "label": "BUILD·REL", "color": "blue"},
    {"type": "deploy", "label": "DEPLOY", "color": "teal"},
    {"type": "release", "label": "RELEASE", "color": "gold"},
    {"type": "request", "label": "REQUEST", "color": "warn"},
    {"type": "commit", "label": "COMMIT", "color": "neutral"},
]

ALL_TYPES = [m["type"] for m in TYPE_META]


def _csv(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def event_types(user: User) -> list[dict]:
    """The type pills this user may ever see, with labels/colors."""
    allowed = set(user.visible_event_types)
    return [m for m in TYPE_META if m["type"] in allowed]


def list_events(user: User, window: str = "7d", types: str = "", envs: str = "",
                q: str = "", user_q: str = "", page: int = 1, size: int = 75) -> dict:
    w = get_world()

    # ---- server-side gates (never trust client filters) --------------------
    allowed_types = set(user.visible_event_types) & set(ALL_TYPES)
    allowed_envs = set(user.visible_envs)
    names = visible_app_names(user)

    cutoff: datetime | None = None
    span = WINDOWS.get(window, WINDOWS["7d"])
    if span is not None:
        cutoff = datetime.now(timezone.utc) - span

    # Client filters, intersected with what the user is allowed to see.
    sel_types = [t for t in _csv(types) if t in allowed_types] or None
    sel_envs = [e for e in _csv(envs) if e in allowed_envs] or None
    ql = (q or "").strip().lower()
    ul = (user_q or "").strip().lower()

    counts_by_type: dict[str, int] = {t: 0 for t in ALL_TYPES if t in allowed_types}
    filtered: list[dict] = []
    for e in w.events:
        if e["type"] not in allowed_types:
            continue
        if e["app"] not in names:
            continue
        # env gate applies to deploys only; non-deploy events pass it
        if e["type"] == "deploy" and e.get("env") and e["env"] not in allowed_envs:
            continue
        if cutoff is not None and e["when"] < cutoff:
            continue
        if sel_envs is not None and e["type"] == "deploy" and e.get("env") not in sel_envs:
            continue
        if ql and not any(ql in str(e.get(k, "")).lower()
                          for k in ("app", "project", "detail", "version")):
            continue
        if ul and ul not in e.get("user", "").lower() and ul not in e.get("email", "").lower():
            continue
        # counts cover the whole filtered window, regardless of the type toggles
        counts_by_type[e["type"]] = counts_by_type.get(e["type"], 0) + 1
        if sel_types is not None and e["type"] not in sel_types:
            continue
        filtered.append(e)

    size = max(1, min(int(size or 75), 200))
    page = max(1, int(page or 1))
    total = len(filtered)
    pages = max(1, (total + size - 1) // size)
    page = min(page, pages)
    chunk = filtered[(page - 1) * size: page * size]

    rows = [{**e, "when": e["when"].isoformat()} for e in chunk]
    return {"rows": rows, "total": total, "page": page, "pages": pages,
            "counts_by_type": counts_by_type}
