"""DB-backed tests exercising TQL field resolution (``build_query`` with a real
Session) through the search API.

The pure parser/tokenizer is covered in ``test_tql.py``; here we drive
``/api/search`` so the compiler resolves project/status/type/priority/assignee/
label *names* to ids against PostgreSQL. Every query is scoped to a per-run
project so assertions are exact and order-independent on a shared database.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

RUN = uuid.uuid4().hex[:8]
NEEDLE = f"needle{RUN}"          # unique word for the summary `~` test
LABEL = f"tqllabel{RUN}"         # unique label so `labels =` matches only ours


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


@pytest.fixture(scope="module")
def admin_me(client, admin_headers):
    return client.get("/api/auth/me", headers=admin_headers).json()


@pytest.fixture(scope="module")
def admin_id(admin_me):
    return admin_me["id"]


def _meta(client, headers):
    types = {t["name"]: t["id"] for t in client.get("/api/meta/issue-types", headers=headers).json()}
    statuses = {s["name"]: s["id"] for s in client.get("/api/meta/statuses", headers=headers).json()}
    priorities = {p["name"]: p["id"] for p in client.get("/api/meta/priorities", headers=headers).json()}
    return types, statuses, priorities


@pytest.fixture(scope="module")
def seeded(client, admin_headers, admin_id):
    """A project with three issues spanning type/status/priority/assignee/labels."""
    key = ("T" + uuid.uuid4().hex[:4]).upper()
    project = client.post(
        "/api/projects", headers=admin_headers, json={"key": key, "name": f"TQL {key}"}
    ).json()
    pid = project["id"]
    types, statuses, priorities = _meta(client, admin_headers)

    def mk(summary, type_name, status_name, priority_name, assignee_id, labels):
        body = {
            "project_id": pid,
            "type_id": types[type_name],
            "summary": summary,
            "status_id": statuses[status_name],
            "priority_id": priorities[priority_name],
            "label_names": labels,
        }
        if assignee_id is not None:
            body["assignee_id"] = assignee_id
        resp = client.post("/api/issues", headers=admin_headers, json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    i1 = mk("alpha tql one", "Bug", "In Progress", "High", admin_id, [LABEL])
    i2 = mk(f"{NEEDLE} beta two", "Story", "To Do", "Highest", None, [])
    i3 = mk("gamma tql three", "Task", "Done", "Medium", admin_id, [])
    return {"key": key, "pid": pid, "i1": i1, "i2": i2, "i3": i3}


def _search(client, headers, tql):
    resp = client.post(
        "/api/search", headers=headers, json={"tql": tql, "page": 1, "page_size": 50}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _keys(result):
    return {item["key"] for item in result["items"]}


# ===========================================================================
# Field resolution via build_query
# ===========================================================================
def test_tql_project_returns_all_seeded(client, admin_headers, seeded):
    res = _search(client, admin_headers, f"project = {seeded['key']}")
    assert res["total"] == 3
    assert _keys(res) == {seeded["i1"]["key"], seeded["i2"]["key"], seeded["i3"]["key"]}


def test_tql_status_quoted_phrase(client, admin_headers, seeded):
    res = _search(client, admin_headers, f'project = {seeded["key"]} AND status = "In Progress"')
    assert _keys(res) == {seeded["i1"]["key"]}


def test_tql_type(client, admin_headers, seeded):
    res = _search(client, admin_headers, f"project = {seeded['key']} AND type = Bug")
    assert _keys(res) == {seeded["i1"]["key"]}


def test_tql_priority_in_list(client, admin_headers, seeded):
    res = _search(
        client, admin_headers, f"project = {seeded['key']} AND priority IN (High, Highest)"
    )
    assert _keys(res) == {seeded["i1"]["key"], seeded["i2"]["key"]}


def test_tql_assignee_by_username(client, admin_headers, seeded, admin_me):
    # Exercises build_query's user-id resolution path (name -> User.id).
    username = admin_me["username"]
    res = _search(
        client, admin_headers, f"project = {seeded['key']} AND assignee = {username}"
    )
    assert _keys(res) == {seeded["i1"]["key"], seeded["i3"]["key"]}


def test_tql_current_user_function_is_unsupported(client, admin_headers, seeded):
    # KNOWN LIMITATION / BUG: the tokenizer splits ``currentUser()`` into the
    # word ``currentUser`` plus ``(`` ``)``, so the parser rejects it as a
    # trailing token. The ``currentuser()`` branch in TQLCompiler._user_ids is
    # therefore unreachable via the API. Documented here as current behavior.
    resp = client.post(
        "/api/search", headers=admin_headers,
        json={"tql": f"project = {seeded['key']} AND assignee = currentUser()",
              "page": 1, "page_size": 10},
    )
    assert resp.status_code == 400


def test_tql_assignee_empty(client, admin_headers, seeded):
    res = _search(client, admin_headers, f"project = {seeded['key']} AND assignee = empty")
    assert _keys(res) == {seeded["i2"]["key"]}


def test_tql_labels(client, admin_headers, seeded):
    res = _search(client, admin_headers, f"project = {seeded['key']} AND labels = {LABEL}")
    assert _keys(res) == {seeded["i1"]["key"]}


def test_tql_status_category_not_done(client, admin_headers, seeded):
    res = _search(
        client, admin_headers, f"project = {seeded['key']} AND statusCategory != done"
    )
    # The Done issue is excluded; the two non-done remain.
    assert _keys(res) == {seeded["i1"]["key"], seeded["i2"]["key"]}


def test_tql_summary_contains(client, admin_headers, seeded):
    res = _search(client, admin_headers, f"project = {seeded['key']} AND summary ~ {NEEDLE}")
    assert _keys(res) == {seeded["i2"]["key"]}


def test_tql_order_by_updated_desc(client, admin_headers, seeded):
    res = _search(client, admin_headers, f"project = {seeded['key']} ORDER BY updated DESC")
    assert res["total"] == 3
    # All three present; ordering applied without error.
    assert len(res["items"]) == 3


# ===========================================================================
# /api/search/validate
# ===========================================================================
def test_validate_accepts_valid_query(client, admin_headers, seeded):
    resp = client.get(
        "/api/search/validate", headers=admin_headers,
        params={"tql": f"project = {seeded['key']} AND status = Done"},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True
    assert resp.json()["error"] is None


def test_validate_rejects_invalid_query(client, admin_headers):
    resp = client.get(
        "/api/search/validate", headers=admin_headers,
        params={"tql": "status IN (Open Closed"},  # missing comma / unclosed list
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
    assert resp.json()["error"]


def test_search_unknown_field_is_400(client, admin_headers):
    resp = client.post(
        "/api/search", headers=admin_headers,
        json={"tql": "bogusfield = 1", "page": 1, "page_size": 10},
    )
    assert resp.status_code == 400
