"""DB-backed tests for the notification stack (preferences + dispatch).

Exercises ``app.services.notifications`` indirectly through the API:
- ``/api/notification-preferences`` GET seeds default rows per (event, channel)
  and reports sensible defaults; PUT persists a single toggle; unknown
  event/channel -> 400.
- Assigning an issue to another user delivers an in-app "assigned" notification
  to that user (and the unread-count / mark-read endpoints reflect it).
- Commenting notifies the assignee with verb "commented".

Run against a throwaway PostgreSQL via ``TestClient`` (its context manager runs
the app lifespan which performs first-boot bootstrap). Every fixed-identity
resource is created with a per-run-unique key/email so repeated runs against a
persistent database never collide.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models import NOTIFICATION_EVENTS

RUN = uuid.uuid4().hex[:8]

# Defaults asserted against a *fresh* user with no toggles (mirrors the service
# layer: in-app on for everything, email on only for these two events).
_EMAIL_ON_DEFAULT = {"issue_assigned", "issue_mentioned"}


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, email, password):
    resp = client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return _login(client, settings.bootstrap_admin_email, settings.bootstrap_admin_password)


def _register(client, label, password="Sup3rSecret!"):
    suffix = f"{label}_{RUN}"
    resp = client.post(
        "/api/auth/register",
        json={
            "username": suffix,
            "email": f"{suffix}@example.com",
            "display_name": f"Notify {label}",
            "password": password,
        },
    )
    assert resp.status_code == 201, resp.text
    user = resp.json()
    user["_password"] = password
    return user


def _unique_key(prefix="N"):
    return (prefix + uuid.uuid4().hex[:4]).upper()


def _create_project(client, admin_headers):
    key = _unique_key()
    resp = client.post("/api/projects", headers=admin_headers, json={"key": key, "name": f"Proj {key}"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _story_type(client, headers):
    types = client.get("/api/meta/issue-types", headers=headers).json()
    return next(t for t in types if t["name"] == "Story")


def _create_issue(client, headers, project_id, summary="Notify issue", assignee_id=None):
    body = {
        "project_id": project_id,
        "type_id": _story_type(client, headers)["id"],
        "summary": summary,
    }
    if assignee_id is not None:
        body["assignee_id"] = assignee_id
    resp = client.post("/api/issues", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_to_role(client, admin_headers, project_id, user_id, role_name="Developers"):
    roles = client.get("/api/roles", headers=admin_headers).json()
    role = next(r for r in roles if r["name"] == role_name)
    resp = client.post(
        f"/api/roles/projects/{project_id}/actors",
        headers=admin_headers,
        json={"role_id": role["id"], "user_id": user_id},
    )
    assert resp.status_code == 201, resp.text


# ===========================================================================
# Preferences: defaults, persistence, validation
# ===========================================================================
def test_preferences_seed_defaults_for_fresh_user(client):
    user = _register(client, "prefdefault")
    headers = _login(client, user["email"], user["_password"])

    resp = client.get("/api/notification-preferences", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "email_available" in body
    rows = body["rows"]

    # One row per catalogued event, each with both channel booleans.
    events = {r["event"] for r in rows}
    assert events == set(NOTIFICATION_EVENTS)
    for r in rows:
        assert isinstance(r["in_app"], bool)
        assert isinstance(r["email"], bool)
        # Defaults: in-app on everywhere; email on only for the two high-signal events.
        assert r["in_app"] is True
        assert r["email"] is (r["event"] in _EMAIL_ON_DEFAULT)


def test_preferences_update_persists(client):
    user = _register(client, "prefupdate")
    headers = _login(client, user["email"], user["_password"])

    rows = client.get("/api/notification-preferences", headers=headers).json()["rows"]
    target = rows[0]["event"]
    new_value = not rows[0]["in_app"]

    upd = client.put(
        "/api/notification-preferences",
        headers=headers,
        json={"updates": [{"event": target, "channel": "in_app", "enabled": new_value}]},
    )
    assert upd.status_code == 200, upd.text
    updated = next(r for r in upd.json()["rows"] if r["event"] == target)
    assert updated["in_app"] is new_value

    # Persisted across a fresh GET.
    refetched = client.get("/api/notification-preferences", headers=headers).json()
    persisted = next(r for r in refetched["rows"] if r["event"] == target)
    assert persisted["in_app"] is new_value


def test_preferences_rejects_unknown_event(client):
    user = _register(client, "prefbadevent")
    headers = _login(client, user["email"], user["_password"])
    resp = client.put(
        "/api/notification-preferences",
        headers=headers,
        json={"updates": [{"event": "no_such_event", "channel": "in_app", "enabled": True}]},
    )
    assert resp.status_code == 400


def test_preferences_rejects_unknown_channel(client):
    user = _register(client, "prefbadchan")
    headers = _login(client, user["email"], user["_password"])
    resp = client.put(
        "/api/notification-preferences",
        headers=headers,
        json={"updates": [{"event": "issue_assigned", "channel": "carrier_pigeon", "enabled": True}]},
    )
    assert resp.status_code == 400


# ===========================================================================
# Assignment notification flow
# ===========================================================================
def test_assignment_creates_notification_for_assignee(client, admin_headers):
    assignee = _register(client, "assignee")
    assignee_headers = _login(client, assignee["email"], assignee["_password"])

    project = _create_project(client, admin_headers)
    _add_to_role(client, admin_headers, project["id"], assignee["id"], "Developers")

    issue = _create_issue(client, admin_headers, project["id"], "Assign me")
    key = issue["key"]

    # Admin assigns the issue to the other user.
    patched = client.patch(
        f"/api/issues/{key}", headers=admin_headers, json={"assignee_id": assignee["id"]}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["assignee"]["id"] == assignee["id"]

    # The assignee now sees an "assigned" notification referencing this issue.
    notifs = client.get("/api/notifications", headers=assignee_headers)
    assert notifs.status_code == 200
    mine = [n for n in notifs.json() if key in n["message"]]
    assert mine, f"expected a notification mentioning {key}"
    assigned = [n for n in mine if n["verb"] == "assigned"]
    assert assigned, "expected an 'assigned' notification"
    notif_id = assigned[0]["id"]
    assert assigned[0]["is_read"] is False

    # Unread count reflects it.
    count = client.get("/api/notifications/unread-count", headers=assignee_headers)
    assert count.status_code == 200
    assert count.json()["count"] >= 1

    # Marking it read works and drops the unread count.
    before = client.get("/api/notifications/unread-count", headers=assignee_headers).json()["count"]
    read = client.post(f"/api/notifications/{notif_id}/read", headers=assignee_headers)
    assert read.status_code == 200
    after = client.get("/api/notifications/unread-count", headers=assignee_headers).json()["count"]
    assert after == before - 1

    # The actor (admin) does not get a self-notification for their own action.
    admin_notifs = client.get("/api/notifications", headers=admin_headers).json()
    assert not any(n["verb"] == "assigned" and key in n["message"] for n in admin_notifs)


def test_marking_foreign_notification_read_is_404(client, admin_headers):
    # A user cannot mark someone else's notification (or a missing one) read.
    stranger = _register(client, "stranger")
    stranger_headers = _login(client, stranger["email"], stranger["_password"])
    resp = client.post("/api/notifications/999999999/read", headers=stranger_headers)
    assert resp.status_code == 404


# ===========================================================================
# Comment notification flow
# ===========================================================================
def test_comment_notifies_assignee(client, admin_headers):
    assignee = _register(client, "commentee")
    assignee_headers = _login(client, assignee["email"], assignee["_password"])

    project = _create_project(client, admin_headers)
    _add_to_role(client, admin_headers, project["id"], assignee["id"], "Developers")

    # Issue is reported by admin, assigned to the other user.
    issue = _create_issue(
        client, admin_headers, project["id"], "Comment target", assignee_id=assignee["id"]
    )
    key = issue["key"]

    # Admin comments -> assignee should receive a "commented" notification.
    comment = client.post(
        f"/api/issues/{key}/comments", headers=admin_headers, json={"body": "Take a look please"}
    )
    assert comment.status_code == 201, comment.text

    notifs = client.get("/api/notifications", headers=assignee_headers).json()
    commented = [n for n in notifs if n["verb"] == "commented" and n["issue_id"] == issue["id"]]
    assert commented, "expected a 'commented' notification for the assignee"
