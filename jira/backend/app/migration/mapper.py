"""Pure functions translating Jira REST JSON into Trackly model kwargs.

Nothing here touches the database or the network; everything is a plain
transformation so it can be unit-tested in isolation.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

# --- status category --------------------------------------------------------

_CATEGORY_MAP = {
    "new": "todo",
    "undefined": "todo",
    "indeterminate": "in_progress",
    "done": "done",
}


def map_status_category(jira_category_key: str | None) -> str:
    """Map a Jira statusCategory key to Trackly's todo/in_progress/done."""
    if not jira_category_key:
        return "todo"
    return _CATEGORY_MAP.get(str(jira_category_key).lower(), "todo")


# --- users ------------------------------------------------------------------

def map_user(jira_user: dict | None) -> dict | None:
    """Map a Jira user object to Trackly User kwargs, synthesizing gaps.

    Returns ``None`` for an empty/unassigned user.
    """
    if not jira_user:
        return None
    # Cloud uses accountId; Server/DC uses key / name.
    external_id = (
        jira_user.get("accountId")
        or jira_user.get("key")
        or jira_user.get("name")
    )
    if not external_id:
        return None

    display_name = (
        jira_user.get("displayName")
        or jira_user.get("name")
        or str(external_id)
    )
    email = jira_user.get("emailAddress") or ""

    username = (
        jira_user.get("name")  # Server/DC login name
        or (email.split("@")[0] if email else None)
        or _slug(external_id)
    )
    if not email:
        email = f"{_slug(external_id)}@imported.local"

    avatar_url = None
    avatars = jira_user.get("avatarUrls") or {}
    if isinstance(avatars, dict) and avatars:
        # Prefer the largest available avatar.
        avatar_url = avatars.get("48x48") or next(iter(avatars.values()), None)

    return {
        "external_id": str(external_id),
        "username": str(username),
        "email": str(email),
        "display_name": str(display_name),
        "avatar_url": avatar_url,
    }


def _slug(value: Any) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(value)).strip("-").lower() or "user"


# --- priority ---------------------------------------------------------------

_PRIORITY_RANKS = {
    "highest": 1,
    "blocker": 1,
    "critical": 1,
    "high": 2,
    "major": 2,
    "medium": 3,
    "normal": 3,
    "low": 4,
    "minor": 4,
    "lowest": 5,
    "trivial": 5,
}


def map_priority_rank(name: str | None) -> int:
    if not name:
        return 3
    return _PRIORITY_RANKS.get(name.strip().lower(), 3)


# --- Atlassian Document Format ----------------------------------------------

def adf_to_text(adf: Any) -> str:
    """Flatten Atlassian Document Format (or a plain string) to markdown-ish text."""
    if adf is None:
        return ""
    if isinstance(adf, str):
        return adf
    if not isinstance(adf, dict):
        return str(adf)
    parts: list[str] = []
    _render_node(adf, parts, list_depth=0)
    text = "".join(parts)
    # Collapse runs of 3+ newlines into a clean paragraph break.
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def _render_node(node: dict, out: list[str], list_depth: int) -> None:
    node_type = node.get("type")
    content = node.get("content") or []

    if node_type == "text":
        out.append(node.get("text", ""))
        return
    if node_type == "hardBreak":
        out.append("\n")
        return
    if node_type == "paragraph":
        for child in content:
            _render_node(child, out, list_depth)
        out.append("\n\n")
        return
    if node_type in ("heading",):
        level = (node.get("attrs") or {}).get("level", 1)
        out.append("#" * int(level) + " ")
        for child in content:
            _render_node(child, out, list_depth)
        out.append("\n\n")
        return
    if node_type == "bulletList":
        for item in content:
            _render_list_item(item, out, list_depth, marker="- ")
        out.append("\n")
        return
    if node_type == "orderedList":
        for idx, item in enumerate(content, start=1):
            _render_list_item(item, out, list_depth, marker=f"{idx}. ")
        out.append("\n")
        return
    if node_type == "codeBlock":
        lang = (node.get("attrs") or {}).get("language") or ""
        inner: list[str] = []
        for child in content:
            _render_node(child, inner, list_depth)
        out.append(f"```{lang}\n{''.join(inner).strip()}\n```\n\n")
        return
    if node_type == "blockquote":
        inner = []
        for child in content:
            _render_node(child, inner, list_depth)
        for line in "".join(inner).strip().splitlines():
            out.append(f"> {line}\n")
        out.append("\n")
        return
    if node_type == "rule":
        out.append("\n---\n\n")
        return
    if node_type == "mention":
        out.append((node.get("attrs") or {}).get("text", "@user"))
        return
    if node_type == "inlineCard":
        out.append((node.get("attrs") or {}).get("url", ""))
        return
    # Unknown node: just descend into its content.
    for child in content:
        _render_node(child, out, list_depth)


def _render_list_item(item: dict, out: list[str], list_depth: int, marker: str) -> None:
    indent = "  " * list_depth
    inner: list[str] = []
    for child in item.get("content") or []:
        _render_node(child, inner, list_depth + 1)
    text = "".join(inner).strip()
    if not text:
        return
    lines = text.splitlines()
    out.append(f"{indent}{marker}{lines[0]}\n")
    for line in lines[1:]:
        if line.strip():
            out.append(f"{indent}  {line}\n")


# --- date helpers -----------------------------------------------------------

def parse_jira_datetime(value: str | None) -> datetime | None:
    """Parse a Jira ISO-8601 timestamp (e.g. 2023-01-02T03:04:05.000+0000)."""
    if not value:
        return None
    raw = value.strip()
    # Normalise '+0000' -> '+00:00' for fromisoformat.
    if len(raw) >= 5 and raw[-5] in "+-" and raw[-3] != ":":
        raw = raw[:-2] + ":" + raw[-2:]
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_jira_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


# --- issue fields -----------------------------------------------------------

def map_issue_fields(jira_issue: dict, lookups: dict | None = None) -> dict:
    """Convert a Jira issue into a dict of resolved scalar fields + raw refs.

    ``lookups`` may carry ``story_points_field_ids`` (a set/list of
    ``customfield_*`` ids identified as the Story Points field by name).
    The importer is responsible for resolving the ``*_ref`` ids into Trackly
    foreign keys using its in-memory maps.
    """
    lookups = lookups or {}
    fields = jira_issue.get("fields") or {}

    summary = (fields.get("summary") or "").strip() or "(no summary)"
    summary = summary[:500]
    description = adf_to_text(fields.get("description"))

    # Story points: scan known customfield ids, else common defaults.
    story_points = None
    sp_ids = lookups.get("story_points_field_ids") or [
        "customfield_10016", "customfield_10026", "customfield_10002",
    ]
    for fid in sp_ids:
        val = fields.get(fid)
        if isinstance(val, (int, float)):
            story_points = float(val)
            break

    issuetype = fields.get("issuetype") or {}
    status = fields.get("status") or {}
    priority = fields.get("priority") or {}
    resolution = fields.get("resolution") or {}
    parent = fields.get("parent") or {}

    # Epic link: prefer the modern parent (if it's an epic) else legacy field.
    epic_key = None
    epic_field_ids = lookups.get("epic_link_field_ids") or ["customfield_10014", "customfield_10008"]
    for fid in epic_field_ids:
        val = fields.get(fid)
        if isinstance(val, str) and val:
            epic_key = val
            break

    labels = [str(l) for l in (fields.get("labels") or []) if l]

    return {
        # scalar fields
        "summary": summary,
        "description": description or None,
        "story_points": story_points,
        "due_date": parse_jira_date(fields.get("duedate")),
        "original_estimate_seconds": _as_int(fields.get("timeoriginalestimate")),
        "remaining_estimate_seconds": _as_int(fields.get("timeestimate")),
        "resolution": (resolution.get("name") if resolution else None),
        "resolved_at": parse_jira_datetime(fields.get("resolutiondate")),
        "created_at": parse_jira_datetime(fields.get("created")),
        "updated_at": parse_jira_datetime(fields.get("updated")),
        "labels": labels,
        # raw refs to be resolved by the importer via its id maps
        "type_ref": str(issuetype.get("id")) if issuetype.get("id") else None,
        "type_name": issuetype.get("name"),
        "type_subtask": bool(issuetype.get("subtask")),
        "status_ref": str(status.get("id")) if status.get("id") else None,
        "status_name": status.get("name"),
        "priority_ref": str(priority.get("id")) if priority.get("id") else None,
        "priority_name": priority.get("name"),
        "assignee": map_user(fields.get("assignee")),
        "reporter": map_user(fields.get("reporter")),
        "parent_key": parent.get("key"),
        "parent_is_epic": ((parent.get("fields") or {}).get("issuetype") or {}).get("name", "").lower() == "epic"
        if parent else False,
        "epic_key": epic_key,
    }


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
