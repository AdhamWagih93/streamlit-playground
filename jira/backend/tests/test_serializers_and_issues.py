"""DB-backed tests for issue serialization and the issue domain service.

Covers ``app.services.serializers`` (IssueDetail shaping + link inversion) and
``app.services.issues`` (apply_update history, allocate_key sequencing,
resolution-on-done) through the real API.

Runs against a throwaway PostgreSQL via ``TestClient``; per-run-unique keys keep
it reliable on a non-pristine database.
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


def _login(client, email, password):
    resp = client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return _login(client, settings.bootstrap_admin_email, settings.bootstrap_admin_password)


def _unique_key(prefix="S"):
    return (prefix + uuid.uuid4().hex[:4]).upper()


def _create_project(client, admin_headers):
    key = _unique_key()
    resp = client.post("/api/projects", headers=admin_headers, json={"key": key, "name": f"Proj {key}"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _type(client, headers, name="Story"):
    types = client.get("/api/meta/issue-types", headers=headers).json()
    return next(t for t in types if t["name"] == name)


def _create_issue(client, headers, project_id, summary="An issue", **extra):
    body = {"project_id": project_id, "type_id": _type(client, headers)["id"], "summary": summary}
    body.update(extra)
    resp = client.post("/api/issues", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _register(client, label, password="Sup3rSecret!"):
    suffix = f"{label}_{RUN}"
    resp = client.post(
        "/api/auth/register",
        json={
            "username": suffix,
            "email": f"{suffix}@example.com",
            "display_name": f"Ser {label}",
            "password": password,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# IssueDetail serialization: labels, components, fix_versions, child arrays
# ===========================================================================
def test_issue_detail_serializes_labels_components_versions(client, admin_headers):
    project = _create_project(client, admin_headers)
    pid = project["id"]

    comp = client.post(
        f"/api/projects/{pid}/components", headers=admin_headers, json={"name": "Core"}
    )
    assert comp.status_code == 201, comp.text
    cid = comp.json()["id"]

    ver = client.post(
        f"/api/projects/{pid}/versions", headers=admin_headers, json={"name": "1.0.0"}
    )
    assert ver.status_code == 201, ver.text
    vid = ver.json()["id"]

    issue = _create_issue(
        client, admin_headers, pid, "Detail issue",
        label_names=["alpha", "beta"],
        component_ids=[cid],
        fix_version_ids=[vid],
    )
    key = issue["key"]

    got = client.get(f"/api/issues/{key}", headers=admin_headers)
    assert got.status_code == 200, got.text
    detail = got.json()

    # labels serialized as a plain list[str].
    assert isinstance(detail["labels"], list)
    assert all(isinstance(x, str) for x in detail["labels"])
    assert sorted(detail["labels"]) == ["alpha", "beta"]

    # components / fix_versions present and correctly populated.
    assert [c["id"] for c in detail["components"]] == [cid]
    assert detail["components"][0]["name"] == "Core"
    assert [v["id"] for v in detail["fix_versions"]] == [vid]

    # Child collections are always present as arrays (even when empty).
    for field in ("comments", "worklogs", "attachments", "subtasks", "links"):
        assert isinstance(detail[field], list)


# ===========================================================================
# Issue links: outward + inverse inward serialization
# ===========================================================================
def test_issue_link_inversion(client, admin_headers):
    project = _create_project(client, admin_headers)
    pid = project["id"]
    a = _create_issue(client, admin_headers, pid, "Issue A")
    b = _create_issue(client, admin_headers, pid, "Issue B")

    link = client.post(
        f"/api/issues/{a['key']}/links",
        headers=admin_headers,
        json={"link_type": "blocks", "target_key": b["key"]},
    )
    assert link.status_code == 201, link.text

    # A shows the outward "blocks" link pointing at B.
    detail_a = client.get(f"/api/issues/{a['key']}", headers=admin_headers).json()
    out = [l for l in detail_a["links"] if l["issue"]["key"] == b["key"]]
    assert out, "A should have an outward link to B"
    assert out[0]["link_type"] == "blocks"

    # B shows the inverse inward "is_blocked_by" link pointing back at A.
    detail_b = client.get(f"/api/issues/{b['key']}", headers=admin_headers).json()
    inward = [l for l in detail_b["links"] if l["issue"]["key"] == a["key"]]
    assert inward, "B should have an inward link from A"
    assert inward[0]["link_type"] == "is_blocked_by"


# ===========================================================================
# apply_update history + resolution on done transition
# ===========================================================================
def test_apply_update_records_history_across_fields(client, admin_headers):
    member = _register(client, "histassignee")

    project = _create_project(client, admin_headers)
    pid = project["id"]
    issue = _create_issue(client, admin_headers, pid, "Original summary")
    key = issue["key"]

    statuses = client.get("/api/meta/statuses", headers=admin_headers).json()
    done = next(s for s in statuses if s["category"] == "done")
    priorities = client.get("/api/meta/priorities", headers=admin_headers).json()
    high = next(p for p in priorities if p["name"] == "High")

    # Separate PATCH calls so each field change is recorded distinctly.
    assert client.patch(
        f"/api/issues/{key}", headers=admin_headers, json={"summary": "Renamed summary"}
    ).status_code == 200
    assert client.patch(
        f"/api/issues/{key}", headers=admin_headers, json={"priority_id": high["id"]}
    ).status_code == 200
    assert client.patch(
        f"/api/issues/{key}", headers=admin_headers, json={"assignee_id": member["id"]}
    ).status_code == 200
    done_resp = client.patch(
        f"/api/issues/{key}", headers=admin_headers, json={"status_id": done["id"]}
    )
    assert done_resp.status_code == 200, done_resp.text

    # Transition to a Done-category status sets a resolution + resolved_at.
    final = client.get(f"/api/issues/{key}", headers=admin_headers).json()
    assert final["status"]["category"] == "done"
    assert final["resolution"] is not None
    assert final["resolved_at"] is not None

    history = client.get(f"/api/issues/{key}/history", headers=admin_headers)
    assert history.status_code == 200
    fields = {h["field"] for h in history.json()}
    # Each distinct change type is captured.
    assert {"summary", "priority", "assignee", "status"} <= fields

    # The summary change records old + new values.
    summary_changes = [h for h in history.json() if h["field"] == "summary"]
    assert summary_changes
    assert summary_changes[-1]["old_value"] == "Original summary"
    assert summary_changes[-1]["new_value"] == "Renamed summary"


# ===========================================================================
# allocate_key: sequential, unique per project
# ===========================================================================
def test_allocate_key_is_sequential_and_unique(client, admin_headers):
    project = _create_project(client, admin_headers)
    pid = project["id"]
    pkey = project["key"]

    keys = [_create_issue(client, admin_headers, pid, f"Seq {i}")["key"] for i in range(4)]

    # A freshly created project starts numbering at 1 and increments by 1.
    assert keys == [f"{pkey}-1", f"{pkey}-2", f"{pkey}-3", f"{pkey}-4"]
    assert len(set(keys)) == len(keys)  # unique
