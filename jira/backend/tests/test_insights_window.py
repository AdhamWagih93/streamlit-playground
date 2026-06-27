"""Time-filter (window) on project and global insights.

The window scopes the *descriptive* stats (totals, breakdowns, velocity) by
issue creation date; the "needs attention" signals stay current-state.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin(client):
    r = client.post("/api/auth/login", data={
        "username": settings.bootstrap_admin_email,
        "password": settings.bootstrap_admin_password,
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def project(client, admin):
    key = "W" + uuid4().hex[:5].upper()
    proj = client.post("/api/projects", headers=admin, json={"key": key, "name": f"Win {key}"})
    assert proj.status_code == 201, proj.text
    pid = proj.json()["id"]
    types = client.get("/api/meta/issue-types", headers=admin).json()
    story = next(t for t in types if t["name"] == "Story")["id"]
    # 3 issues; one unassigned (drives a current "attention" signal).
    for i in range(3):
        body = {"project_id": pid, "type_id": story, "summary": f"win issue {i}"}
        assert client.post("/api/issues", headers=admin, json=body).status_code == 201
    return {"id": pid, "key": key}


def _proj_stats(client, admin, key, **params):
    r = client.get(f"/api/analytics/projects/{key}", headers=admin, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_window_default_is_all(client, admin, project):
    d = _proj_stats(client, admin, project["key"])
    assert d["window"]["period"] == "all"
    assert d["window"]["start"] is None and d["window"]["end"] is None
    assert d["total_issues"] == 3


def test_period_shortcut_includes_recent_and_echoes_window(client, admin, project):
    d = _proj_stats(client, admin, project["key"], period="30d")
    assert d["window"]["period"] == "30d"
    assert d["window"]["start"] is not None and d["window"]["end"] is not None
    # All 3 were created just now → within the last 30 days.
    assert d["total_issues"] == 3


def test_future_start_excludes_everything(client, admin, project):
    d = _proj_stats(client, admin, project["key"], **{"from": "2999-01-01"})
    assert d["window"]["period"] == "custom"
    assert d["total_issues"] == 0
    assert d["by_status"] == [] and d["by_type"] == []


def test_past_end_excludes_everything(client, admin, project):
    d = _proj_stats(client, admin, project["key"], to="2000-01-01")
    assert d["total_issues"] == 0


def test_wide_custom_range_includes_everything(client, admin, project):
    d = _proj_stats(client, admin, project["key"], **{"from": "2000-01-01", "to": "2999-01-01"})
    assert d["total_issues"] == 3


def test_attention_ignores_the_window(client, admin, project):
    """Even with a window that excludes all created issues, current-state
    attention signals (e.g. unassigned open work) still surface."""
    d = _proj_stats(client, admin, project["key"], to="2000-01-01")
    assert d["total_issues"] == 0
    # The 3 issues are open + unassigned right now → attention is non-empty.
    assert d["attention_score"] > 0
    assert any(a["key"] == "unassigned" for a in d["attention"])


def test_invalid_window_params_400(client, admin, project):
    assert client.get(f"/api/analytics/projects/{project['key']}",
                      headers=admin, params={"period": "lots"}).status_code == 400
    assert client.get(f"/api/analytics/projects/{project['key']}",
                      headers=admin, params={"from": "not-a-date"}).status_code == 400


def test_overview_window(client, admin, project):
    r = client.get("/api/analytics/overview", headers=admin, params={"period": "7d"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window"]["period"] == "7d"
    # Narrow past window → descriptive totals collapse to 0 instance-wide…
    past = client.get("/api/analytics/overview", headers=admin, params={"to": "2000-01-01"}).json()
    assert past["total_issues"] == 0
    # …but the attention roll-up is current-state, so it still reflects open work.
    assert past["projects_needing_attention"] >= 1
