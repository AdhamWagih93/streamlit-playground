"""Functional test suite for Trackly.

These are real in-process functional tests: they exercise the actual FastAPI
application, its routers, authentication, RBAC engine and live PostgreSQL
queries via ``TestClient``. The ``with TestClient(app) as c`` context manager
runs the app lifespan, which performs first-boot bootstrap (creating tables,
seeding default metadata/RBAC and the bootstrap admin user).

They run against an isolated, throwaway database and must NOT assume or touch
production data. To stay reliable on a non-fresh database (the suite may be
re-run against the same Postgres), every fixed-identity resource is created
with a per-run-unique key/name, and helpers delete-then-create where a stable
key is required. Nothing here hard-fails because a previous run left data.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

# A short token unique to this test process. Appended to keys/names/emails so
# repeated runs against a persistent database never collide.
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
    resp = client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def admin_headers(client) -> dict[str, str]:
    return _login(
        client,
        settings.bootstrap_admin_email,
        settings.bootstrap_admin_password,
    )


def _unique_key(prefix: str = "F") -> str:
    """A valid, short, uppercase-letter project key unique to this run."""
    return (prefix + uuid.uuid4().hex[:4]).upper()


def create_project(client, admin_headers, key=None, name=None) -> dict:
    """Create a project, removing any pre-existing project with the same key."""
    key = (key or _unique_key()).upper()
    existing = client.get(f"/api/projects/{key}", headers=admin_headers)
    if existing.status_code == 200:
        client.delete(f"/api/projects/{existing.json()['id']}", headers=admin_headers)
    resp = client.post(
        "/api/projects",
        headers=admin_headers,
        json={"key": key, "name": name or f"Project {key}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _story_type(client, headers) -> dict:
    types = client.get("/api/meta/issue-types", headers=headers).json()
    return next(t for t in types if t["name"] == "Story")


def create_issue(client, headers, project_id, summary="An issue", labels=None) -> dict:
    story = _story_type(client, headers)
    resp = client.post(
        "/api/issues",
        headers=headers,
        json={
            "project_id": project_id,
            "type_id": story["id"],
            "summary": summary,
            "label_names": labels or [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def register_user(client, label: str, password: str = "Sup3rSecret!") -> dict:
    """Register a fresh local user unique to this run. Returns the user dict +
    the plaintext password under ``_password`` for convenience."""
    suffix = f"{label}_{RUN}"
    payload = {
        "username": suffix,
        "email": f"{suffix}@example.com",
        "display_name": f"Func {label}",
        "password": password,
    }
    resp = client.post("/api/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    user = resp.json()
    user["_password"] = password
    return user


# ===========================================================================
# 1. Authentication
# ===========================================================================
def test_auth_policy_is_public(client):
    resp = client.get("/api/auth/policy")
    assert resp.status_code == 200
    body = resp.json()
    assert "allow_local_login" in body
    assert "allow_self_registration" in body


def test_auth_providers_is_public_list(client):
    resp = client.get("/api/auth/providers")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_login_and_me_is_admin(client, admin_headers):
    me = client.get("/api/auth/me", headers=admin_headers)
    assert me.status_code == 200
    assert me.json()["is_admin"] is True
    assert me.json()["email"] == settings.bootstrap_admin_email


def test_login_with_bad_password_401(client):
    resp = client.post(
        "/api/auth/login",
        data={"username": settings.bootstrap_admin_email, "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_refresh_valid_and_invalid(client):
    login = client.post(
        "/api/auth/login",
        data={
            "username": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert login.status_code == 200
    refresh_token = login.json()["refresh_token"]

    ok = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert ok.status_code == 200
    assert "access_token" in ok.json()

    bad = client.post("/api/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert bad.status_code == 401


def test_register_success_and_duplicate_conflict(client):
    suffix = f"dup_{RUN}"
    payload = {
        "username": suffix,
        "email": f"{suffix}@example.com",
        "display_name": "Dup User",
        "password": "Sup3rSecret!",
    }
    first = client.post("/api/auth/register", json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["is_admin"] is False

    dup = client.post("/api/auth/register", json=payload)
    assert dup.status_code == 409


def test_patch_me_updates_display_name(client):
    user = register_user(client, "patchme")
    headers = _login(client, user["email"], user["_password"])
    resp = client.patch(
        "/api/auth/me", headers=headers, json={"display_name": "Renamed Member"}
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Renamed Member"
    # Privilege escalation via self-update is ignored.
    esc = client.patch("/api/auth/me", headers=headers, json={"is_admin": True})
    assert esc.status_code == 200
    assert esc.json()["is_admin"] is False


# ===========================================================================
# 2. Auth settings + registration gating
# ===========================================================================
def test_auth_settings_and_registration_gating(client, admin_headers):
    # Snapshot current settings so we can restore them at the end.
    current = client.get("/api/admin/auth-settings", headers=admin_headers)
    assert current.status_code == 200
    original = current.json()

    def put(body):
        r = client.put("/api/admin/auth-settings", headers=admin_headers, json=body)
        assert r.status_code == 200, r.text
        return r.json()

    try:
        # --- Self-registration disabled -> 403 -------------------------------
        put(
            {
                "allow_local_login": True,
                "allow_self_registration": False,
                "registration_allowed_domains": None,
            }
        )
        blocked = client.post(
            "/api/auth/register",
            json={
                "username": f"blocked_{RUN}",
                "email": f"blocked_{RUN}@example.com",
                "display_name": "Blocked",
                "password": "Sup3rSecret!",
            },
        )
        assert blocked.status_code == 403

        # --- Domain allowlist ------------------------------------------------
        put(
            {
                "allow_local_login": True,
                "allow_self_registration": True,
                "registration_allowed_domains": "allowed-domain.com",
            }
        )
        disallowed = client.post(
            "/api/auth/register",
            json={
                "username": f"baddomain_{RUN}",
                "email": f"baddomain_{RUN}@notallowed-domain.com",
                "display_name": "Bad Domain",
                "password": "Sup3rSecret!",
            },
        )
        assert disallowed.status_code == 403

        allowed = client.post(
            "/api/auth/register",
            json={
                "username": f"gooddomain_{RUN}",
                "email": f"gooddomain_{RUN}@allowed-domain.com",
                "display_name": "Good Domain",
                "password": "Sup3rSecret!",
            },
        )
        assert allowed.status_code == 201, allowed.text
    finally:
        # Reset to a permissive policy so the rest of the suite keeps working.
        put(
            {
                "allow_local_login": True,
                "allow_self_registration": True,
                "access_token_minutes": original.get("access_token_minutes"),
                "refresh_token_minutes": original.get("refresh_token_minutes"),
                "registration_allowed_domains": None,
            }
        )


def test_auth_settings_requires_admin(client):
    user = register_user(client, "notadmin_settings")
    headers = _login(client, user["email"], user["_password"])
    assert client.get("/api/admin/auth-settings", headers=headers).status_code == 403


# ===========================================================================
# 3. Users
# ===========================================================================
def test_users_list_get_create_patch(client, admin_headers):
    # List + ?q= filter (the admin is always present).
    listing = client.get("/api/users", headers=admin_headers)
    assert listing.status_code == 200
    assert isinstance(listing.json(), list)

    filtered = client.get(
        "/api/users", headers=admin_headers, params={"q": settings.bootstrap_admin_username}
    )
    assert filtered.status_code == 200
    assert any(
        u["username"] == settings.bootstrap_admin_username for u in filtered.json()
    )

    # Admin create.
    suffix = f"created_{RUN}"
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": suffix,
            "email": f"{suffix}@example.com",
            "display_name": "Created User",
            "password": "Sup3rSecret!",
        },
    )
    assert created.status_code == 201, created.text
    uid = created.json()["id"]

    # Get by id.
    got = client.get(f"/api/users/{uid}", headers=admin_headers)
    assert got.status_code == 200
    assert got.json()["id"] == uid

    # Admin patch.
    patched = client.patch(
        f"/api/users/{uid}", headers=admin_headers, json={"display_name": "Updated Name"}
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "Updated Name"


def test_users_create_requires_admin(client):
    user = register_user(client, "notadmin_users")
    headers = _login(client, user["email"], user["_password"])
    resp = client.post(
        "/api/users",
        headers=headers,
        json={
            "username": f"x_{RUN}",
            "email": f"x_{RUN}@example.com",
            "display_name": "X",
            "password": "Sup3rSecret!",
        },
    )
    assert resp.status_code == 403


# ===========================================================================
# 4. Metadata
# ===========================================================================
def test_meta_endpoints(client, admin_headers):
    types = client.get("/api/meta/issue-types", headers=admin_headers).json()
    assert {t["name"] for t in types} >= {"Story", "Bug", "Task"}

    statuses = client.get("/api/meta/statuses", headers=admin_headers).json()
    assert {s["category"] for s in statuses} >= {"todo", "in_progress", "done"}

    priorities = client.get("/api/meta/priorities", headers=admin_headers)
    assert priorities.status_code == 200
    assert len(priorities.json()) >= 1

    labels = client.get("/api/meta/labels", headers=admin_headers)
    assert labels.status_code == 200
    assert isinstance(labels.json(), list)


# ===========================================================================
# 5. Projects + components + versions
# ===========================================================================
def test_project_crud_components_versions(client, admin_headers):
    project = create_project(client, admin_headers)
    pid = project["id"]
    key = project["key"]

    # The default permission scheme is applied to a freshly created project.
    assert project["permission_scheme"] is not None

    # Fetch by key and by id.
    by_key = client.get(f"/api/projects/{key}", headers=admin_headers)
    assert by_key.status_code == 200
    by_id = client.get(f"/api/projects/{pid}", headers=admin_headers)
    assert by_id.status_code == 200
    assert by_id.json()["id"] == pid

    # Patch.
    patched = client.patch(
        f"/api/projects/{pid}", headers=admin_headers, json={"name": "Renamed Project"}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed Project"

    # Components CRUD.
    comp = client.post(
        f"/api/projects/{pid}/components",
        headers=admin_headers,
        json={"name": "Backend", "description": "Server side"},
    )
    assert comp.status_code == 201, comp.text
    cid = comp.json()["id"]
    assert client.get(f"/api/projects/{pid}/components", headers=admin_headers).status_code == 200
    upd = client.patch(
        f"/api/projects/{pid}/components/{cid}",
        headers=admin_headers,
        json={"name": "Backend API", "description": "Server side"},
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "Backend API"
    assert client.delete(
        f"/api/projects/{pid}/components/{cid}", headers=admin_headers
    ).status_code == 200

    # Versions CRUD.
    ver = client.post(
        f"/api/projects/{pid}/versions",
        headers=admin_headers,
        json={"name": "1.0.0", "description": "First release"},
    )
    assert ver.status_code == 201, ver.text
    vid = ver.json()["id"]
    assert client.get(f"/api/projects/{pid}/versions", headers=admin_headers).status_code == 200
    vupd = client.patch(
        f"/api/projects/{pid}/versions/{vid}",
        headers=admin_headers,
        json={"name": "1.0.1", "released": True},
    )
    assert vupd.status_code == 200
    assert vupd.json()["released"] is True
    assert client.delete(
        f"/api/projects/{pid}/versions/{vid}", headers=admin_headers
    ).status_code == 200


# ===========================================================================
# 6. RBAC: groups, roles, permission schemes, global permissions
# ===========================================================================
def test_groups_crud_and_membership(client, admin_headers):
    member = register_user(client, "group_member")

    name = f"func-group-{RUN}"
    created = client.post(
        "/api/groups",
        headers=admin_headers,
        json={"name": name, "description": "A functional-test group"},
    )
    assert created.status_code == 201, created.text
    gid = created.json()["id"]

    assert client.get("/api/groups", headers=admin_headers).status_code == 200
    got = client.get(f"/api/groups/{gid}", headers=admin_headers)
    assert got.status_code == 200

    patched = client.patch(
        f"/api/groups/{gid}",
        headers=admin_headers,
        json={"name": name, "description": "Updated description"},
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "Updated description"

    # Add + remove member.
    add = client.post(
        f"/api/groups/{gid}/members", headers=admin_headers, json={"user_id": member["id"]}
    )
    assert add.status_code == 200, add.text
    assert any(m["id"] == member["id"] for m in add.json()["members"])
    rem = client.delete(f"/api/groups/{gid}/members/{member['id']}", headers=admin_headers)
    assert rem.status_code == 200

    # Cleanup.
    assert client.delete(f"/api/groups/{gid}", headers=admin_headers).status_code == 200


def test_default_roles_present(client, admin_headers):
    roles = client.get("/api/roles", headers=admin_headers)
    assert roles.status_code == 200
    names = {r["name"] for r in roles.json()}
    assert {"Administrators", "Developers", "Viewers"} <= names


def test_permission_schemes_catalog_and_grants(client, admin_headers):
    # Catalog lists both global and project permissions.
    catalog = client.get("/api/permission-schemes/catalog", headers=admin_headers)
    assert catalog.status_code == 200
    cat = catalog.json()
    global_keys = {p["key"] for p in cat["global_permissions"]}
    project_keys = {p["key"] for p in cat["project_permissions"]}
    assert "ADMINISTER" in global_keys
    assert "BROWSE_PROJECTS" in project_keys

    assert client.get("/api/permission-schemes", headers=admin_headers).status_code == 200

    # Create a scheme, add a grant, then remove it.
    scheme = client.post(
        "/api/permission-schemes",
        headers=admin_headers,
        json={"name": f"func-scheme-{RUN}", "description": "Functional test scheme"},
    )
    assert scheme.status_code == 201, scheme.text
    sid = scheme.json()["id"]

    grant = client.post(
        f"/api/permission-schemes/{sid}/grants",
        headers=admin_headers,
        json={
            "permission": "BROWSE_PROJECTS",
            "holder_type": "role",
            "holder_value": "Developers",
        },
    )
    assert grant.status_code == 201, grant.text
    grant_id = grant.json()["id"]

    detail = client.get(f"/api/permission-schemes/{sid}", headers=admin_headers)
    assert detail.status_code == 200
    assert any(g["id"] == grant_id for g in detail.json()["grants"])

    deleted = client.delete(
        f"/api/permission-schemes/{sid}/grants/{grant_id}", headers=admin_headers
    )
    assert deleted.status_code == 200

    # Cleanup the scheme (non-default schemes are deletable).
    assert client.delete(f"/api/permission-schemes/{sid}", headers=admin_headers).status_code == 200


def test_global_permissions_crud(client, admin_headers):
    member = register_user(client, "globalperm")
    created = client.post(
        "/api/admin/global-permissions",
        headers=admin_headers,
        json={
            "permission": "BULK_CHANGE",
            "holder_type": "user",
            "holder_value": str(member["id"]),
        },
    )
    assert created.status_code == 201, created.text
    grant_id = created.json()["id"]

    listing = client.get("/api/admin/global-permissions", headers=admin_headers)
    assert listing.status_code == 200
    assert any(g["id"] == grant_id for g in listing.json())

    assert client.delete(
        f"/api/admin/global-permissions/{grant_id}", headers=admin_headers
    ).status_code == 200


# ===========================================================================
# 7. Permission enforcement
# ===========================================================================
def test_permission_enforcement_flow(client, admin_headers):
    member = register_user(client, "member")
    member_headers = _login(client, member["email"], member["_password"])

    # A plain member is denied admin and group-write endpoints...
    assert client.get("/api/admin/mail", headers=member_headers).status_code == 403
    assert client.post(
        "/api/groups", headers=member_headers, json={"name": f"x-{RUN}"}
    ).status_code == 403
    # ...but the read-only permission catalog is allowed to any authenticated user.
    assert client.get(
        "/api/permission-schemes/catalog", headers=member_headers
    ).status_code == 200

    project = create_project(client, admin_headers)
    pid = project["id"]

    # Before being granted a role the member cannot administer the project.
    assert client.patch(
        f"/api/projects/{pid}", headers=member_headers, json={"name": "Nope"}
    ).status_code == 403

    # Grant the member the project's Administrators role.
    roles = client.get("/api/roles", headers=admin_headers).json()
    admins_role = next(r for r in roles if r["name"] == "Administrators")
    actor = client.post(
        f"/api/roles/projects/{pid}/actors",
        headers=admin_headers,
        json={"role_id": admins_role["id"], "user_id": member["id"]},
    )
    assert actor.status_code == 201, actor.text

    # Now the member has ADMINISTER_PROJECTS through the role.
    patched = client.patch(
        f"/api/projects/{pid}", headers=member_headers, json={"name": "Member Renamed"}
    )
    assert patched.status_code == 200
    comp = client.post(
        f"/api/projects/{pid}/components",
        headers=member_headers,
        json={"name": "Member Component"},
    )
    assert comp.status_code == 201, comp.text

    # A different outsider has no access to this project's insights...
    outsider = register_user(client, "outsider")
    outsider_headers = _login(client, outsider["email"], outsider["_password"])
    assert client.get(
        f"/api/analytics/projects/{project['key']}", headers=outsider_headers
    ).status_code == 403
    # ...but /analytics/my succeeds and reports zero projects for them.
    mine = client.get("/api/analytics/my", headers=outsider_headers)
    assert mine.status_code == 200
    assert mine.json()["total_projects"] == 0


# ===========================================================================
# 8. Issue lifecycle
# ===========================================================================
def test_issue_full_lifecycle(client, admin_headers):
    project = create_project(client, admin_headers)
    pid = project["id"]

    # Create with labels.
    issue = create_issue(client, admin_headers, pid, "Lifecycle issue", labels=["alpha", "beta"])
    key = issue["key"]
    assert key.startswith(f"{project['key']}-")

    # Labels echoed back as a list[str].
    got = client.get(f"/api/issues/{key}", headers=admin_headers)
    assert got.status_code == 200
    assert sorted(got.json()["labels"]) == ["alpha", "beta"]

    # Transition to a Done-category status.
    statuses = client.get("/api/meta/statuses", headers=admin_headers).json()
    done = next(s for s in statuses if s["category"] == "done")
    patched = client.patch(
        f"/api/issues/{key}", headers=admin_headers, json={"status_id": done["id"]}
    )
    assert patched.status_code == 200
    assert patched.json()["status"]["category"] == "done"

    # Comments: create / list / patch / delete.
    comment = client.post(
        f"/api/issues/{key}/comments", headers=admin_headers, json={"body": "First comment"}
    )
    assert comment.status_code == 201, comment.text
    coid = comment.json()["id"]
    assert client.get(f"/api/issues/{key}/comments", headers=admin_headers).status_code == 200
    cupd = client.patch(
        f"/api/issues/{key}/comments/{coid}", headers=admin_headers, json={"body": "Edited"}
    )
    assert cupd.status_code == 200
    assert cupd.json()["body"] == "Edited"
    assert client.delete(
        f"/api/issues/{key}/comments/{coid}", headers=admin_headers
    ).status_code == 200

    # Worklog: "2h 30m" -> 9000 seconds.
    work = client.post(
        f"/api/issues/{key}/worklogs",
        headers=admin_headers,
        json={"time_spent": "2h 30m", "comment": "Did work"},
    )
    assert work.status_code == 201, work.text
    assert work.json()["time_spent_seconds"] == 2 * 3600 + 30 * 60
    worklogs = client.get(f"/api/issues/{key}/worklogs", headers=admin_headers)
    assert worklogs.status_code == 200
    assert len(worklogs.json()) >= 1

    # History records the status change.
    history = client.get(f"/api/issues/{key}/history", headers=admin_headers)
    assert history.status_code == 200
    fields = {h["field"] for h in history.json()}
    assert "status" in fields

    # Link to a second issue.
    other = create_issue(client, admin_headers, pid, "Link target")
    link = client.post(
        f"/api/issues/{key}/links",
        headers=admin_headers,
        json={"link_type": "relates to", "target_key": other["key"]},
    )
    assert link.status_code == 201, link.text
    assert link.json()["issue"]["key"] == other["key"]

    # Rank: move this issue to sit after the other one.
    rank = client.put(
        f"/api/issues/{key}/rank", headers=admin_headers, json={"after_id": other["id"]}
    )
    assert rank.status_code == 200


def test_issue_attachment_upload_list_download(client, admin_headers):
    project = create_project(client, admin_headers)
    issue = create_issue(client, admin_headers, project["id"], "Attachment issue")
    key = issue["key"]

    content = b"hello attachment world"
    up = client.post(
        f"/api/issues/{key}/attachments",
        headers=admin_headers,
        files={"file": ("note.txt", content, "text/plain")},
    )
    assert up.status_code == 201, up.text
    aid = up.json()["id"]
    assert up.json()["filename"] == "note.txt"
    assert up.json()["size_bytes"] == len(content)

    listing = client.get(f"/api/issues/{key}/attachments", headers=admin_headers)
    assert listing.status_code == 200
    assert any(a["id"] == aid for a in listing.json())

    download = client.get(f"/api/issues/attachments/{aid}/download", headers=admin_headers)
    assert download.status_code == 200
    assert download.content == content


# ===========================================================================
# 9. Agile boards & sprints
# ===========================================================================
def test_agile_board_and_sprint_flow(client, admin_headers):
    project = create_project(client, admin_headers)
    pid = project["id"]

    # A scrum board is auto-created with the project.
    boards = client.get("/api/agile/boards", headers=admin_headers, params={"project_id": pid})
    assert boards.status_code == 200
    assert len(boards.json()) >= 1
    board_id = boards.json()[0]["id"]

    assert client.get(f"/api/agile/boards/{board_id}/board", headers=admin_headers).status_code == 200
    assert client.get(f"/api/agile/boards/{board_id}/backlog", headers=admin_headers).status_code == 200

    # Create + start a sprint.
    sprint = client.post(
        f"/api/agile/boards/{board_id}/sprints",
        headers=admin_headers,
        json={"name": f"Sprint {RUN}", "goal": "Ship it"},
    )
    assert sprint.status_code == 201, sprint.text
    sid = sprint.json()["id"]
    assert sprint.json()["state"] == "future"

    started = client.post(f"/api/agile/sprints/{sid}/start", headers=admin_headers, json={})
    assert started.status_code == 200
    assert started.json()["state"] == "active"

    # Move an issue into the active sprint via the rank endpoint.
    issue = create_issue(client, admin_headers, pid, "Sprint issue")
    moved = client.put(
        f"/api/issues/{issue['key']}/rank", headers=admin_headers, json={"sprint_id": sid}
    )
    assert moved.status_code == 200
    assert moved.json()["sprint_id"] == sid

    # The active-sprint board view now surfaces the issue.
    view = client.get(f"/api/agile/boards/{board_id}/board", headers=admin_headers)
    assert view.status_code == 200
    assert view.json()["active_sprint"]["id"] == sid
    all_issue_keys = {
        i["key"] for col in view.json()["columns"] for i in col["issues"]
    }
    assert issue["key"] in all_issue_keys

    # Complete the sprint.
    completed = client.post(f"/api/agile/sprints/{sid}/complete", headers=admin_headers)
    assert completed.status_code == 200
    assert completed.json()["state"] == "closed"


# ===========================================================================
# 10. Search & saved filters
# ===========================================================================
def test_search_validate_and_saved_filters(client, admin_headers):
    project = create_project(client, admin_headers)
    create_issue(client, admin_headers, project["id"], "Searchable issue")

    found = client.post(
        "/api/search",
        headers=admin_headers,
        json={"tql": f"project = {project['key']}", "page": 1, "page_size": 10},
    )
    assert found.status_code == 200
    assert found.json()["total"] >= 1

    valid = client.get(
        "/api/search/validate", headers=admin_headers, params={"tql": f"project = {project['key']}"}
    )
    assert valid.status_code == 200
    assert valid.json()["valid"] is True

    # Saved filters: create / list / delete.
    saved = client.post(
        "/api/search/filters",
        headers=admin_headers,
        json={"name": f"filter-{RUN}", "query": f"project = {project['key']}"},
    )
    assert saved.status_code == 201, saved.text
    fid = saved.json()["id"]
    listing = client.get("/api/search/filters", headers=admin_headers)
    assert listing.status_code == 200
    assert any(f["id"] == fid for f in listing.json())
    assert client.delete(f"/api/search/filters/{fid}", headers=admin_headers).status_code == 200


# ===========================================================================
# 11. Analytics
# ===========================================================================
def test_analytics_project_overview_and_my(client, admin_headers):
    project = create_project(client, admin_headers)
    pid = project["id"]
    create_issue(client, admin_headers, pid, "Analytics issue 1")
    issue2 = create_issue(client, admin_headers, pid, "Analytics issue 2")

    # Move issue2 to a done status so by_status has more than one bucket.
    statuses = client.get("/api/meta/statuses", headers=admin_headers).json()
    done = next(s for s in statuses if s["category"] == "done")
    client.patch(
        f"/api/issues/{issue2['key']}", headers=admin_headers, json={"status_id": done["id"]}
    )

    stats = client.get(f"/api/analytics/projects/{project['key']}", headers=admin_headers)
    assert stats.status_code == 200
    body = stats.json()
    for key in ("by_status", "by_type", "velocity"):
        assert key in body
    assert body["total_issues"] >= 2
    # Per-status counts are internally consistent with the total.
    assert sum(item["count"] for item in body["by_status"]) == body["total_issues"]
    assert sum(item["count"] for item in body["by_type"]) == body["total_issues"]

    overview = client.get("/api/analytics/overview", headers=admin_headers)
    assert overview.status_code == 200
    assert overview.json()["scope"] == "all"
    assert "by_status" in overview.json()

    mine = client.get("/api/analytics/my", headers=admin_headers)
    assert mine.status_code == 200
    assert mine.json()["scope"] == "mine"


def test_analytics_overview_requires_admin(client):
    user = register_user(client, "notadmin_overview")
    headers = _login(client, user["email"], user["_password"])
    assert client.get("/api/analytics/overview", headers=headers).status_code == 403


# ===========================================================================
# 12. Admin config secrecy (write-only secrets)
# ===========================================================================
def test_jira_connection_token_is_write_only(client, admin_headers):
    created = client.post(
        "/api/admin/jira-connections",
        headers=admin_headers,
        json={
            "name": f"jira-{RUN}",
            "base_url": "https://example.atlassian.net",
            "api_token": "super-secret-token",
        },
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    assert created.json()["token_set"] is True
    assert "api_token" not in created.json()
    assert "api_token_enc" not in created.json()

    got = client.get("/api/admin/jira-connections", headers=admin_headers)
    assert got.status_code == 200
    conn = next(c for c in got.json() if c["id"] == cid)
    assert conn["token_set"] is True
    assert "api_token" not in conn

    # Cleanup.
    assert client.delete(f"/api/admin/jira-connections/{cid}", headers=admin_headers).status_code == 200


def test_identity_provider_secret_is_write_only(client, admin_headers):
    created = client.post(
        "/api/admin/identity-providers",
        headers=admin_headers,
        json={
            "name": f"ldap-{RUN}",
            "provider_type": "ldap",
            "ldap_host": "ldap.example.com",
            "ldap_bind_dn": "cn=admin,dc=example,dc=com",
            "ldap_bind_password": "very-secret",
        },
    )
    assert created.status_code == 201, created.text
    idp_id = created.json()["id"]
    assert created.json()["ldap_bind_password_set"] is True
    assert "ldap_bind_password" not in created.json()
    assert "ldap_bind_password_enc" not in created.json()

    got = client.get("/api/admin/identity-providers", headers=admin_headers)
    assert got.status_code == 200
    idp = next(i for i in got.json() if i["id"] == idp_id)
    assert idp["ldap_bind_password_set"] is True
    assert "ldap_bind_password" not in idp

    # Cleanup.
    assert client.delete(
        f"/api/admin/identity-providers/{idp_id}", headers=admin_headers
    ).status_code == 200


def test_mail_password_is_write_only(client, admin_headers):
    # Snapshot, mutate, then restore so we never leave odd mail state behind.
    original = client.get("/api/admin/mail", headers=admin_headers)
    assert original.status_code == 200
    orig = original.json()

    try:
        updated = client.put(
            "/api/admin/mail",
            headers=admin_headers,
            json={
                "enabled": False,
                "host": "smtp.example.com",
                "port": 587,
                "username": "mailer",
                "password": "smtp-secret",
                "from_address": "noreply@example.com",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["password_set"] is True
        assert "password" not in updated.json()

        got = client.get("/api/admin/mail", headers=admin_headers)
        assert got.status_code == 200
        assert got.json()["password_set"] is True
        assert got.json()["host"] == "smtp.example.com"
    finally:
        client.put(
            "/api/admin/mail",
            headers=admin_headers,
            json={
                "enabled": orig["enabled"],
                "host": orig.get("host"),
                "port": orig.get("port", 587),
                "username": orig.get("username"),
                # Omit password -> keeps whatever was there before this test.
                "use_tls": orig.get("use_tls", True),
                "use_ssl": orig.get("use_ssl", False),
                "from_address": orig.get("from_address"),
                "from_name": orig.get("from_name", "Trackly"),
            },
        )


# ===========================================================================
# 13. Notification preferences
# ===========================================================================
def test_notification_preferences_get_update_persist(client, admin_headers):
    initial = client.get("/api/notification-preferences", headers=admin_headers)
    assert initial.status_code == 200
    rows = initial.json()["rows"]
    assert len(rows) >= 1
    target_event = rows[0]["event"]
    current_in_app = rows[0]["in_app"]
    new_value = not current_in_app

    upd = client.put(
        "/api/notification-preferences",
        headers=admin_headers,
        json={"updates": [{"event": target_event, "channel": "in_app", "enabled": new_value}]},
    )
    assert upd.status_code == 200
    updated_row = next(r for r in upd.json()["rows"] if r["event"] == target_event)
    assert updated_row["in_app"] is new_value

    # Confirm it persisted across a fresh GET.
    refetched = client.get("/api/notification-preferences", headers=admin_headers)
    persisted_row = next(r for r in refetched.json()["rows"] if r["event"] == target_event)
    assert persisted_row["in_app"] is new_value


def test_notification_preferences_rejects_unknown_event(client, admin_headers):
    resp = client.put(
        "/api/notification-preferences",
        headers=admin_headers,
        json={"updates": [{"event": "does_not_exist", "channel": "in_app", "enabled": True}]},
    )
    assert resp.status_code == 400
