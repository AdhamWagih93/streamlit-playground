"""Functional tests for the RBAC engine (app.services.permissions) end-to-end.

Exercised through the real API over a throwaway PostgreSQL via ``TestClient``.
The ``with TestClient(app) as c`` context runs the lifespan/bootstrap (tables +
default roles, scheme, admin). Every fixed-identity resource uses a per-run
unique key so repeated runs against a persistent database never collide.

Note on enforcement surface: project-permission enforcement lives on the
project/analytics/roles routers (BROWSE_PROJECTS for reads, ADMINISTER_PROJECTS
for project admin). We assert against those gated endpoints. See the module
docstring in test_analytics for the issue-router enforcement gap finding.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

RUN = uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def admin_headers(client) -> dict[str, str]:
    return _login(client, settings.bootstrap_admin_email, settings.bootstrap_admin_password)


def _unique_key(prefix: str = "P") -> str:
    return (prefix + uuid.uuid4().hex[:4]).upper()


def create_project(client, admin_headers, key=None, name=None) -> dict:
    key = (key or _unique_key()).upper()
    existing = client.get(f"/api/projects/{key}", headers=admin_headers)
    if existing.status_code == 200:
        client.delete(f"/api/projects/{existing.json()['id']}", headers=admin_headers)
    resp = client.post(
        "/api/projects", headers=admin_headers, json={"key": key, "name": name or f"Project {key}"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def register_user(client, label: str, password: str = "Sup3rSecret!") -> dict:
    suffix = f"perm_{label}_{RUN}"
    resp = client.post(
        "/api/auth/register",
        json={
            "username": suffix,
            "email": f"{suffix}@example.com",
            "display_name": f"Perm {label}",
            "password": password,
        },
    )
    assert resp.status_code == 201, resp.text
    user = resp.json()
    user["_password"] = password
    user["_headers"] = _login(client, user["email"], password)
    return user


def _role_id(client, admin_headers, name: str) -> int:
    roles = client.get("/api/roles", headers=admin_headers).json()
    return next(r["id"] for r in roles if r["name"] == name)


def _create_group(client, admin_headers, label: str) -> dict:
    name = f"perm-grp-{label}-{RUN}"
    # Delete-if-exists for re-run safety.
    for g in client.get("/api/groups", headers=admin_headers).json():
        if g["name"] == name:
            client.delete(f"/api/groups/{g['id']}", headers=admin_headers)
    resp = client.post("/api/groups", headers=admin_headers, json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# 1. Developer role grants BROWSE; outsiders are denied
# ===========================================================================
def test_developer_role_grants_browse_access(client, admin_headers):
    project = create_project(client, admin_headers)
    pid, key = project["id"], project["key"]
    member = register_user(client, "dev")
    outsider = register_user(client, "dev_out")

    insights = f"/api/analytics/projects/{key}"
    actors = f"/api/roles/projects/{pid}/actors"

    # Before any grant, a plain user cannot browse the project.
    assert client.get(insights, headers=member["_headers"]).status_code == 403
    assert client.get(actors, headers=member["_headers"]).status_code == 403

    # Grant the Developers role (which carries BROWSE_PROJECTS) to the member.
    dev_role = _role_id(client, admin_headers, "Developers")
    granted = client.post(
        actors, headers=admin_headers, json={"role_id": dev_role, "user_id": member["id"]}
    )
    assert granted.status_code == 201, granted.text

    # Now the member can browse the project's insights and role actors.
    assert client.get(insights, headers=member["_headers"]).status_code == 200
    assert client.get(actors, headers=member["_headers"]).status_code == 200

    # A different outsider still cannot.
    assert client.get(insights, headers=outsider["_headers"]).status_code == 403


# ===========================================================================
# 2. Administrator role grants project admin; revoked on actor removal
# ===========================================================================
def test_administrator_role_grants_then_revokes_admin(client, admin_headers):
    project = create_project(client, admin_headers)
    pid = project["id"]
    member = register_user(client, "admin_member")
    h = member["_headers"]

    # Before the grant the member cannot administer the project.
    assert client.patch(f"/api/projects/{pid}", headers=h, json={"name": "Nope"}).status_code == 403
    assert client.post(
        f"/api/projects/{pid}/components", headers=h, json={"name": "Blocked"}
    ).status_code == 403

    admins_role = _role_id(client, admin_headers, "Administrators")
    actor = client.post(
        f"/api/roles/projects/{pid}/actors",
        headers=admin_headers,
        json={"role_id": admins_role, "user_id": member["id"]},
    )
    assert actor.status_code == 201, actor.text
    actor_id = actor.json()["id"]

    # Now the member can administer the project.
    patched = client.patch(f"/api/projects/{pid}", headers=h, json={"name": "Member Renamed"})
    assert patched.status_code == 200, patched.text
    comp = client.post(f"/api/projects/{pid}/components", headers=h, json={"name": "Allowed"})
    assert comp.status_code == 201, comp.text

    # Remove the actor -> access is revoked.
    removed = client.delete(
        f"/api/roles/projects/{pid}/actors/{actor_id}", headers=admin_headers
    )
    assert removed.status_code == 200
    assert client.patch(
        f"/api/projects/{pid}", headers=h, json={"name": "Nope again"}
    ).status_code == 403


# ===========================================================================
# 3. Group-based access: role granted to a group flows to its members
# ===========================================================================
def test_group_membership_confers_project_role(client, admin_headers):
    project = create_project(client, admin_headers)
    pid, key = project["id"], project["key"]
    member = register_user(client, "grp_member")
    group = _create_group(client, admin_headers, "access")

    insights = f"/api/analytics/projects/{key}"
    # No access before joining the group / before the group has the role.
    assert client.get(insights, headers=member["_headers"]).status_code == 403

    # Put the user in the group...
    add = client.post(
        f"/api/groups/{group['id']}/members", headers=admin_headers, json={"user_id": member["id"]}
    )
    assert add.status_code == 200, add.text

    # ...and grant the GROUP the Developers role on the project.
    dev_role = _role_id(client, admin_headers, "Developers")
    granted = client.post(
        f"/api/roles/projects/{pid}/actors",
        headers=admin_headers,
        json={"role_id": dev_role, "group_id": group["id"]},
    )
    assert granted.status_code == 201, granted.text

    # The member now browses the project through the group.
    assert client.get(insights, headers=member["_headers"]).status_code == 200

    # Removing the user from the group revokes the inherited access.
    rem = client.delete(
        f"/api/groups/{group['id']}/members/{member['id']}", headers=admin_headers
    )
    assert rem.status_code == 200
    assert client.get(insights, headers=member["_headers"]).status_code == 403


# ===========================================================================
# 4. Site admin bypasses everything; outsiders denied
# ===========================================================================
def test_site_admin_bypass_and_outsider_denied(client, admin_headers):
    project = create_project(client, admin_headers)
    pid, key = project["id"], project["key"]
    outsider = register_user(client, "lone_outsider")
    h = outsider["_headers"]

    # The site admin administers a project with no explicit role grant.
    assert client.patch(
        f"/api/projects/{pid}", headers=admin_headers, json={"name": "Admin Renamed"}
    ).status_code == 200
    assert client.get(f"/api/analytics/projects/{key}", headers=admin_headers).status_code == 200

    # The outsider is denied both browse and admin.
    assert client.get(f"/api/analytics/projects/{key}", headers=h).status_code == 403
    assert client.patch(f"/api/projects/{pid}", headers=h, json={"name": "x"}).status_code == 403
    # /analytics/my succeeds but reports no accessible projects for the outsider.
    mine = client.get("/api/analytics/my", headers=h)
    assert mine.status_code == 200
    assert mine.json()["total_projects"] == 0


# ===========================================================================
# 5. Global permission (BROWSE_USERS) granted to a group gates /api/groups
# ===========================================================================
def test_global_browse_users_permission_via_group(client, admin_headers):
    member = register_user(client, "browse_member")
    outsider = register_user(client, "browse_out")
    group = _create_group(client, admin_headers, "browseusers")

    # Put the member in the group.
    assert client.post(
        f"/api/groups/{group['id']}/members", headers=admin_headers, json={"user_id": member["id"]}
    ).status_code == 200

    # Neither can list groups yet (reads are gated on BROWSE_USERS / site admin).
    assert client.get("/api/groups", headers=member["_headers"]).status_code == 403
    assert client.get("/api/groups", headers=outsider["_headers"]).status_code == 403

    # Grant the GROUP the global BROWSE_USERS (USER_PICKER) permission.
    grant = client.post(
        "/api/admin/global-permissions",
        headers=admin_headers,
        json={"permission": "USER_PICKER", "holder_type": "group", "holder_value": group["name"]},
    )
    assert grant.status_code == 201, grant.text
    grant_id = grant.json()["id"]

    try:
        # The member (in the group) can now list groups; the outsider still cannot.
        assert client.get("/api/groups", headers=member["_headers"]).status_code == 200
        assert client.get("/api/groups", headers=outsider["_headers"]).status_code == 403
    finally:
        client.delete(f"/api/admin/global-permissions/{grant_id}", headers=admin_headers)

    # After revocation the member loses access again.
    assert client.get("/api/groups", headers=member["_headers"]).status_code == 403
