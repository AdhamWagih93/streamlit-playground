"""Functional tests for the attention/insights engine (app.services.analytics).

Run end-to-end through the API over a throwaway PostgreSQL. Each test seeds a
fresh, uniquely-keyed project so signal counts are deterministic and re-runs on
a persistent database never collide.

Finding (not fixed): the issues router enforces no project permissions, so the
attention signals are exercised here purely via the (gated) analytics endpoints;
seeding issues needs only an authenticated user, not a project grant.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

RUN = uuid.uuid4().hex[:8]

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        data={
            "username": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def meta(client, admin_headers) -> dict:
    types = {t["name"]: t for t in client.get("/api/meta/issue-types", headers=admin_headers).json()}
    priorities = {p["name"]: p for p in client.get("/api/meta/priorities", headers=admin_headers).json()}
    statuses = client.get("/api/meta/statuses", headers=admin_headers).json()
    me = client.get("/api/auth/me", headers=admin_headers).json()
    return {"types": types, "priorities": priorities, "statuses": statuses, "admin_id": me["id"]}


def _unique_key(prefix: str = "AN") -> str:
    return (prefix + uuid.uuid4().hex[:4]).upper()


def create_project(client, admin_headers) -> dict:
    key = _unique_key()
    existing = client.get(f"/api/projects/{key}", headers=admin_headers)
    if existing.status_code == 200:
        client.delete(f"/api/projects/{existing.json()['id']}", headers=admin_headers)
    resp = client.post(
        "/api/projects", headers=admin_headers, json={"key": key, "name": f"Analytics {key}"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_issue(
    client,
    admin_headers,
    meta,
    project_id,
    summary,
    *,
    type_name="Story",
    priority_name=None,
    assignee_id=None,
    due_date=None,
    story_points=None,
) -> dict:
    body = {
        "project_id": project_id,
        "type_id": meta["types"][type_name]["id"],
        "summary": summary,
    }
    if priority_name:
        body["priority_id"] = meta["priorities"][priority_name]["id"]
    if assignee_id is not None:
        body["assignee_id"] = assignee_id
    if due_date is not None:
        body["due_date"] = due_date
    if story_points is not None:
        body["story_points"] = story_points
    resp = client.post("/api/issues", headers=admin_headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _attn(items: list[dict]) -> dict[str, dict]:
    return {it["key"]: it for it in items}


# ===========================================================================
# 1. Per-project attention signals
# ===========================================================================
def test_project_attention_signals(client, admin_headers, meta):
    project = create_project(client, admin_headers)
    pid, key = project["id"], project["key"]
    admin_id = meta["admin_id"]

    # Overdue: open + past due date (assigned, so it doesn't also count unassigned).
    create_issue(client, admin_headers, meta, pid, "Overdue work",
                 assignee_id=admin_id, due_date="2020-01-01")
    # Unassigned: open with no owner.
    create_issue(client, admin_headers, meta, pid, "Nobody owns this")
    # High priority: Highest, open, assigned.
    create_issue(client, admin_headers, meta, pid, "Urgent thing",
                 priority_name="Highest", assignee_id=admin_id)
    # Open bug: type Bug, open, assigned.
    create_issue(client, admin_headers, meta, pid, "A defect",
                 type_name="Bug", assignee_id=admin_id)
    # Blocked: A blocks B -> B is the blocked (target) issue.
    a = create_issue(client, admin_headers, meta, pid, "Blocker A", assignee_id=admin_id)
    b = create_issue(client, admin_headers, meta, pid, "Blocked B", assignee_id=admin_id)
    link = client.post(
        f"/api/issues/{a['key']}/links",
        headers=admin_headers,
        json={"link_type": "blocks", "target_key": b["key"]},
    )
    assert link.status_code == 201, link.text

    stats = client.get(f"/api/analytics/projects/{key}", headers=admin_headers)
    assert stats.status_code == 200, stats.text
    body = stats.json()

    items = body["attention"]
    by_key = _attn(items)
    # Every controllable signal fired.
    assert {"overdue", "high_priority", "blocked", "unassigned", "open_bugs"} <= set(by_key)
    assert by_key["overdue"]["count"] == 1
    assert by_key["unassigned"]["count"] == 1
    assert by_key["high_priority"]["count"] == 1
    assert by_key["open_bugs"]["count"] == 1
    assert by_key["blocked"]["count"] == 1

    # The blocked target shows up in that signal's samples.
    blocked_keys = {s["key"] for s in by_key["blocked"]["samples"]}
    assert b["key"] in blocked_keys

    # Score is positive and items are ordered highest-severity first.
    assert body["attention_score"] > 0
    ranks = [SEVERITY_RANK[it["severity"]] for it in items]
    assert ranks == sorted(ranks), f"attention items not severity-ordered: {ranks}"


# ===========================================================================
# 1b. Low-priority guidance: backlog + actively-worked-but-low signals
# ===========================================================================
def test_low_priority_guidance(client, admin_headers, meta):
    project = create_project(client, admin_headers)
    pid, key = project["id"], project["key"]
    admin_id = meta["admin_id"]

    # Two low-priority, open, assigned issues (so they don't also count as
    # unassigned/overdue and muddy the score).
    create_issue(client, admin_headers, meta, pid, "Low prio backlog",
                 priority_name="Low", assignee_id=admin_id)
    working = create_issue(client, admin_headers, meta, pid, "Lowest, being worked",
                           priority_name="Lowest", assignee_id=admin_id)
    in_progress = next(s for s in meta["statuses"] if s["category"] == "in_progress")
    moved = client.patch(f"/api/issues/{working['key']}", headers=admin_headers,
                         json={"status_id": in_progress["id"]})
    assert moved.status_code == 200, moved.text

    body = client.get(f"/api/analytics/projects/{key}", headers=admin_headers).json()
    by_key = _attn(body["attention"])

    # Low-priority backlog is surfaced with action-oriented, low-severity guidance.
    assert "low_priority" in by_key
    assert by_key["low_priority"]["count"] == 2
    assert by_key["low_priority"]["severity"] == "low"
    assert "defer" in by_key["low_priority"]["description"].lower()

    # The one that's in progress is flagged as mild waste (medium severity).
    assert by_key["low_priority_wip"]["count"] == 1
    assert by_key["low_priority_wip"]["severity"] == "medium"

    # Backlog volume alone must NOT inflate the urgency score: only the single
    # in-progress low item contributes (weight 1); the backlog bucket is weight 0.
    assert body["attention_score"] == 1

    # Items remain ordered highest-severity first.
    ranks = [SEVERITY_RANK[it["severity"]] for it in body["attention"]]
    assert ranks == sorted(ranks)


# ===========================================================================
# 1c. Per-component breakdown (incl. a "No component" bucket)
# ===========================================================================
def test_by_component_breakdown(client, admin_headers, meta):
    project = create_project(client, admin_headers)
    pid, key = project["id"], project["key"]

    backend = client.post(f"/api/projects/{pid}/components", headers=admin_headers,
                          json={"name": "Backend"})
    assert backend.status_code == 201, backend.text
    cid = backend.json()["id"]
    # An empty component must not appear in the breakdown.
    client.post(f"/api/projects/{pid}/components", headers=admin_headers, json={"name": "Frontend"})

    client.post("/api/issues", headers=admin_headers, json={
        "project_id": pid, "type_id": meta["types"]["Story"]["id"],
        "summary": "Has a component", "component_ids": [cid]})
    create_issue(client, admin_headers, meta, pid, "No component on this one")

    stats = client.get(f"/api/analytics/projects/{key}", headers=admin_headers).json()
    bc = {c["label"]: c["count"] for c in stats["by_component"]}
    assert bc.get("Backend") == 1
    assert bc.get("No component") == 1
    assert "Frontend" not in bc  # zero-issue components are omitted


# ===========================================================================
# 2. Active sprint health
# ===========================================================================
def test_sprint_health(client, admin_headers, meta):
    project = create_project(client, admin_headers)
    pid, key = project["id"], project["key"]
    admin_id = meta["admin_id"]

    boards = client.get("/api/agile/boards", headers=admin_headers, params={"project_id": pid})
    assert boards.status_code == 200
    board_id = boards.json()[0]["id"]

    sprint = client.post(
        f"/api/agile/boards/{board_id}/sprints",
        headers=admin_headers,
        json={"name": f"Health Sprint {RUN}", "goal": "Burn it down"},
    )
    assert sprint.status_code == 201, sprint.text
    sid = sprint.json()["id"]

    # Two estimated, open issues moved into the sprint.
    for summary, pts in (("Sprint A", 3), ("Sprint B", 5)):
        issue = create_issue(client, admin_headers, meta, pid, summary,
                             assignee_id=admin_id, story_points=pts)
        moved = client.put(
            f"/api/issues/{issue['key']}/rank", headers=admin_headers, json={"sprint_id": sid}
        )
        assert moved.status_code == 200, moved.text

    started = client.post(f"/api/agile/sprints/{sid}/start", headers=admin_headers, json={})
    assert started.status_code == 200, started.text

    stats = client.get(f"/api/analytics/projects/{key}", headers=admin_headers).json()
    health = stats["sprint_health"]
    assert health is not None
    assert health["sprint_id"] == sid
    assert health["total_points"] == 8.0
    assert 0.0 <= health["percent_complete"] <= 1.0
    # Nothing is done yet -> two incomplete issues, 0% complete.
    assert health["incomplete_issues"] == 2
    assert health["percent_complete"] == 0.0


# ===========================================================================
# 3. Instance-wide overview rolls up the seeded signals
# ===========================================================================
def test_overview_rollup(client, admin_headers, meta):
    project = create_project(client, admin_headers)
    pid = project["id"]
    admin_id = meta["admin_id"]

    create_issue(client, admin_headers, meta, pid, "OV overdue",
                 assignee_id=admin_id, due_date="2020-02-02")
    create_issue(client, admin_headers, meta, pid, "OV unassigned")
    create_issue(client, admin_headers, meta, pid, "OV urgent",
                 priority_name="High", assignee_id=admin_id)
    a = create_issue(client, admin_headers, meta, pid, "OV blocker", assignee_id=admin_id)
    b = create_issue(client, admin_headers, meta, pid, "OV blocked", assignee_id=admin_id)
    client.post(
        f"/api/issues/{a['key']}/links",
        headers=admin_headers,
        json={"link_type": "blocks", "target_key": b["key"]},
    )

    overview = client.get("/api/analytics/overview", headers=admin_headers)
    assert overview.status_code == 200, overview.text
    body = overview.json()

    assert body["scope"] == "all"
    # Totals reflect at least the data we just seeded.
    assert body["total_overdue"] >= 1
    assert body["total_high_priority_open"] >= 1
    assert body["total_unassigned_open"] >= 1
    assert body["total_blocked"] >= 1
    assert body["projects_needing_attention"] >= 1

    # Projects are sorted by attention_score descending.
    scores = [p["attention_score"] for p in body["projects"]]
    assert scores == sorted(scores, reverse=True)

    # The cross-project most-urgent list is populated.
    assert len(body["top_attention"]) >= 1

    # Our seeded project is flagged as needing attention.
    ours = next(p for p in body["projects"] if p["project_id"] == pid)
    assert ours["needs_attention"] is True
    assert ours["attention_score"] > 0


# ===========================================================================
# 4. A clean project (only done work) is all-clear
# ===========================================================================
def test_clean_project_has_no_attention(client, admin_headers, meta):
    project = create_project(client, admin_headers)
    pid, key = project["id"], project["key"]

    issue = create_issue(client, admin_headers, meta, pid, "Already finished")
    done = next(s for s in meta["statuses"] if s["category"] == "done")
    patched = client.patch(
        f"/api/issues/{issue['key']}", headers=admin_headers, json={"status_id": done["id"]}
    )
    assert patched.status_code == 200, patched.text

    stats = client.get(f"/api/analytics/projects/{key}", headers=admin_headers).json()
    assert stats["attention"] == []
    assert stats["attention_score"] == 0
    assert stats["sprint_health"] is None
    # The done issue is still counted in totals, just not flagged.
    assert stats["total_issues"] == 1
    assert stats["closed_issues"] == 1
