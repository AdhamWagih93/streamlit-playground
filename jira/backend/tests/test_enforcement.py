"""Default-deny RBAC enforcement on issue/comment/project/search endpoints.

Proves that, unless a permission scheme grant explicitly allows it, a user can
neither see nor act on a project's issues — and that access is governed by the
permission scheme (roles + grants), settable per project.
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


def _admin(client) -> dict:
    r = client.post("/api/auth/login", data={
        "username": settings.bootstrap_admin_email,
        "password": settings.bootstrap_admin_password,
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _register_and_login(client, label: str) -> tuple[int, dict]:
    email = f"{label}-{uuid4().hex[:8]}@example.com"
    reg = client.post("/api/auth/register", json={
        "username": f"{label}_{uuid4().hex[:6]}", "email": email,
        "display_name": label.title(), "password": "password123",
    })
    assert reg.status_code == 201, reg.text
    uid = reg.json()["id"]
    tok = client.post("/api/auth/login", data={"username": email, "password": "password123"})
    assert tok.status_code == 200, tok.text
    return uid, {"Authorization": f"Bearer {tok.json()['access_token']}"}


def _role_ids(client, H) -> dict:
    return {r["name"]: r["id"] for r in client.get("/api/roles", headers=H).json()}


def _new_project(client, H) -> dict:
    key = "E" + uuid4().hex[:5].upper()
    r = client.post("/api/projects", headers=H, json={"key": key, "name": f"Enf {key}"})
    assert r.status_code == 201, r.text
    return r.json()


def _story_type(client, H) -> int:
    types = client.get("/api/meta/issue-types", headers=H).json()
    return next(t for t in types if t["name"] == "Story")["id"]


def _new_issue(client, H, pid: int, tid: int) -> dict:
    r = client.post("/api/issues", headers=H, json={
        "project_id": pid, "type_id": tid, "summary": "Gated issue"})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
def test_outsider_is_denied_everything(client):
    """A user with no role on a project sees/does nothing (default-deny)."""
    A = _admin(client)
    tid = _story_type(client, A)
    proj = _new_project(client, A)
    pid, key = proj["id"], proj["key"]
    issue = _new_issue(client, A, pid, tid)
    ikey = issue["key"]

    _, O = _register_and_login(client, "outsider")

    assert client.get(f"/api/projects/{key}", headers=O).status_code == 403
    assert client.get(f"/api/issues/{ikey}", headers=O).status_code == 403
    assert client.get(f"/api/issues/{ikey}/comments", headers=O).status_code == 403
    assert client.post(f"/api/issues/{ikey}/comments", headers=O, json={"body": "hi"}).status_code == 403
    # Cannot create an issue in the project.
    assert client.post("/api/issues", headers=O, json={
        "project_id": pid, "type_id": tid, "summary": "nope"}).status_code == 403
    # Search never reveals issues from a non-browsable project.
    sr = client.post("/api/search", headers=O, json={"tql": f"project = {key}", "page": 1, "page_size": 10})
    assert sr.status_code == 200 and sr.json()["total"] == 0
    # The project doesn't appear in their project list.
    assert key not in {p["key"] for p in client.get("/api/projects", headers=O).json()}


def test_developer_role_grants_scoped_actions(client):
    """The default scheme's Developers role can browse/create/edit/comment, but
    NOT delete issues (DELETE_ISSUES isn't granted to Developers)."""
    A = _admin(client)
    tid = _story_type(client, A)
    roles = _role_ids(client, A)
    proj = _new_project(client, A)
    pid, key = proj["id"], proj["key"]
    issue = _new_issue(client, A, pid, tid)
    ikey = issue["key"]
    admin_comment = client.post(f"/api/issues/{ikey}/comments", headers=A, json={"body": "admin note"}).json()

    dev_id, D = _register_and_login(client, "dev")
    # Grant access by adding the user to the Developers role on this project.
    add = client.post(f"/api/roles/projects/{pid}/actors", headers=A,
                      json={"role_id": roles["Developers"], "user_id": dev_id})
    assert add.status_code in (200, 201), add.text

    # Allowed:
    assert client.get(f"/api/issues/{ikey}", headers=D).status_code == 200
    assert client.post("/api/issues", headers=D, json={
        "project_id": pid, "type_id": tid, "summary": "dev story"}).status_code == 201
    assert client.patch(f"/api/issues/{ikey}", headers=D, json={"summary": "edited by dev"}).status_code == 200
    own = client.post(f"/api/issues/{ikey}/comments", headers=D, json={"body": "dev comment"})
    assert own.status_code == 201
    # Search now returns the project's issues for the developer.
    sr = client.post("/api/search", headers=D, json={"tql": f"project = {key}", "page": 1, "page_size": 10})
    assert sr.status_code == 200 and sr.json()["total"] >= 1

    # Denied:
    assert client.delete(f"/api/issues/{ikey}", headers=D).status_code == 403   # no DELETE_ISSUES
    # Can delete OWN comment (DELETE_OWN_COMMENTS) but not someone else's (no DELETE_ALL).
    assert client.delete(f"/api/issues/{ikey}/comments/{own.json()['id']}", headers=D).status_code in (200, 204)
    assert client.delete(f"/api/issues/{ikey}/comments/{admin_comment['id']}", headers=D).status_code == 403


def test_custom_scheme_browse_only(client):
    """Access is governed by the project's permission scheme: a scheme granting
    only BROWSE_PROJECTS to a user lets them view but not create."""
    A = _admin(client)
    tid = _story_type(client, A)
    proj = _new_project(client, A)
    pid, key = proj["id"], proj["key"]
    issue = _new_issue(client, A, pid, tid)
    ikey = issue["key"]

    viewer_id, V = _register_and_login(client, "viewer")

    # A scheme that grants ONLY browse, to this specific user.
    scheme = client.post("/api/permission-schemes", headers=A,
                         json={"name": f"BrowseOnly-{uuid4().hex[:6]}"}).json()
    g = client.post(f"/api/permission-schemes/{scheme['id']}/grants", headers=A, json={
        "permission": "BROWSE_PROJECTS", "holder_type": "user", "holder_value": str(viewer_id)})
    assert g.status_code in (200, 201), g.text
    assign = client.put(f"/api/permission-schemes/projects/{pid}", headers=A, json={"scheme_id": scheme["id"]})
    assert assign.status_code == 200, assign.text

    # Browse allowed, create denied — exactly what the scheme grants.
    assert client.get(f"/api/issues/{ikey}", headers=V).status_code == 200
    assert client.post("/api/issues", headers=V, json={
        "project_id": pid, "type_id": tid, "summary": "should fail"}).status_code == 403


def test_site_admin_bypasses_all(client):
    """Site admins are never blocked by project permissions."""
    A = _admin(client)
    tid = _story_type(client, A)
    proj = _new_project(client, A)
    issue = _new_issue(client, A, proj["id"], tid)
    assert client.get(f"/api/issues/{issue['key']}", headers=A).status_code == 200
    assert client.delete(f"/api/issues/{issue['key']}", headers=A).status_code in (200, 204)
