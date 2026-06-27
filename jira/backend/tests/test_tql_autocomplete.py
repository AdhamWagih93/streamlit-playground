"""TQL autocomplete support: the schema catalog and value suggestions."""
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
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def project(client, admin):
    key = "AC" + uuid4().hex[:4].upper()
    proj = client.post("/api/projects", headers=admin, json={"key": key, "name": f"AC {key}"})
    assert proj.status_code == 201, proj.text
    story = next(t for t in client.get("/api/meta/issue-types", headers=admin).json()
                 if t["name"] == "Story")["id"]
    client.post("/api/issues", headers=admin, json={
        "project_id": proj.json()["id"], "type_id": story, "summary": "ac issue",
        "label_names": ["aclabel"]})
    return {"key": key}


def test_tql_schema(client, admin):
    s = client.get("/api/search/tql-schema", headers=admin)
    assert s.status_code == 200, s.text
    body = s.json()
    names = {f["name"] for f in body["fields"]}
    assert {"project", "status", "assignee", "priority", "labels", "created"} <= names
    # each field declares operators + a type
    for f in body["fields"]:
        assert f["operators"] and f["type"]
    assert "ORDER BY" in body["keywords"]
    assert "currentUser()" in body["functions"]
    assert body["examples"] and all("query" in e for e in body["examples"])


def test_tql_schema_requires_auth(client):
    assert client.get("/api/search/tql-schema").status_code == 401


def test_values_project_rbac(client, admin, project):
    r = client.get("/api/search/values", headers=admin, params={"field": "project", "q": project["key"][:3]})
    assert r.status_code == 200
    assert project["key"] in {v["value"] for v in r.json()}


def test_values_status_and_category(client, admin):
    st = client.get("/api/search/values", headers=admin, params={"field": "status"}).json()
    assert any("Progress" in (v.get("label") or v["value"]) for v in st)
    cat = client.get("/api/search/values", headers=admin, params={"field": "statusCategory"}).json()
    assert {"todo", "in_progress", "done"} == {v["value"] for v in cat}


def test_values_priority_and_assignee(client, admin):
    pr = {v["value"] for v in client.get("/api/search/values", headers=admin, params={"field": "priority"}).json()}
    assert {"Highest", "High", "Medium", "Low", "Lowest"} <= pr
    asg = client.get("/api/search/values", headers=admin, params={"field": "assignee"}).json()
    vals = {v["value"] for v in asg}
    assert "currentUser()" in vals and "empty" in vals


def test_values_labels(client, admin, project):
    labs = {v["value"] for v in client.get("/api/search/values", headers=admin, params={"field": "labels", "q": "ac"}).json()}
    assert "aclabel" in labs


def test_values_outsider_project_scope(client):
    email = f"acout-{uuid4().hex[:8]}@example.com"
    client.post("/api/auth/register", json={
        "username": f"acout{uuid4().hex[:6]}", "email": email, "display_name": "AcOut", "password": "password123"})
    tok = client.post("/api/auth/login", data={"username": email, "password": "password123"}).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}
    # An outsider sees no projects in suggestions (RBAC-scoped).
    r = client.get("/api/search/values", headers=H, params={"field": "project"})
    assert r.status_code == 200 and r.json() == []
