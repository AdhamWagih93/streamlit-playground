"""End-to-end smoke test against a live database via FastAPI's TestClient.

Requires a reachable PostgreSQL (provided by the CI ``postgres`` service, or a
local instance). The TestClient context manager runs the app lifespan, which
performs first-boot bootstrap (tables + default data + admin user).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client: TestClient) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        data={
            "username": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_health(client):
    assert client.get("/health").status_code == 200
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_login_and_me(client):
    headers = _login(client)
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["is_admin"] is True


def test_default_metadata_seeded(client):
    headers = _login(client)
    types = client.get("/api/meta/issue-types", headers=headers).json()
    statuses = client.get("/api/meta/statuses", headers=headers).json()
    assert {t["name"] for t in types} >= {"Story", "Bug", "Task"}
    assert {s["category"] for s in statuses} >= {"todo", "in_progress", "done"}


def test_project_and_issue_flow(client):
    headers = _login(client)
    types = client.get("/api/meta/issue-types", headers=headers).json()
    story = next(t for t in types if t["name"] == "Story")

    # Idempotent: remove a leftover CI project so the test works on any database,
    # not only a pristine one (CI itself uses a fresh Postgres each run).
    existing = client.get("/api/projects/CI", headers=headers)
    if existing.status_code == 200:
        client.delete(f"/api/projects/{existing.json()['id']}", headers=headers)

    proj = client.post(
        "/api/projects", headers=headers, json={"key": "CI", "name": "CI Project"}
    )
    assert proj.status_code == 201, proj.text
    pid = proj.json()["id"]
    # The creator becomes a project Administrator and the default scheme applies.
    assert proj.json()["permission_scheme"] is not None

    issue = client.post(
        "/api/issues",
        headers=headers,
        json={"project_id": pid, "type_id": story["id"], "summary": "First issue",
              "label_names": ["ci"]},
    )
    assert issue.status_code == 201, issue.text
    key = issue.json()["key"]
    assert key.startswith("CI-")

    got = client.get(f"/api/issues/{key}", headers=headers)
    assert got.status_code == 200
    assert got.json()["labels"] == ["ci"]

    search = client.post(
        "/api/search", headers=headers, json={"tql": "project = CI", "page": 1, "page_size": 10}
    )
    assert search.status_code == 200
    assert search.json()["total"] >= 1


def test_analytics(client):
    headers = _login(client)
    overview = client.get("/api/analytics/overview", headers=headers)
    assert overview.status_code == 200
    assert "by_status" in overview.json()
