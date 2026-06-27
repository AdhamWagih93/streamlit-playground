"""Site-admin impersonation ("view as user").

A site admin can adopt an existing user's view and permissions, then return to
their own account.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import decode_token


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
def admin_id(client, admin):
    return client.get("/api/auth/me", headers=admin).json()["id"]


@pytest.fixture(scope="module")
def member(client, admin):
    """A non-admin user with NO project roles (so default-deny applies)."""
    email = f"imp-{uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={
        "username": f"imp_{uuid4().hex[:6]}", "email": email,
        "display_name": "Impersonated Member", "password": "password123"})
    uid = r.json()["id"]
    tok = client.post("/api/auth/login", data={"username": email, "password": "password123"}).json()["access_token"]
    return {"id": uid, "email": email, "headers": {"Authorization": f"Bearer {tok}"}}


def test_impersonation_adopts_user_view(client, admin, admin_id, member):
    # Admin creates a project the member can't see.
    key = "IM" + uuid4().hex[:5].upper()
    client.post("/api/projects", headers=admin, json={"key": key, "name": "Imp Proj"})

    tokens = client.post(f"/api/admin/impersonate/{member['id']}", headers=admin)
    assert tokens.status_code == 200, tokens.text
    access = tokens.json()["access_token"]
    # The token records the real admin as the actor.
    claims = decode_token(access)
    assert claims["sub"] == str(member["id"]) and claims["act"] == admin_id and claims["imp"] is True

    H = {"Authorization": f"Bearer {access}"}
    # Now the session IS the member: their identity, their (empty) view, no admin.
    assert client.get("/api/auth/me", headers=H).json()["id"] == member["id"]
    assert client.get("/api/admin/mail", headers=H).status_code == 403
    assert key not in {p["key"] for p in client.get("/api/projects", headers=H).json()}


def test_stop_impersonation_returns_to_admin(client, admin, admin_id, member):
    access = client.post(f"/api/admin/impersonate/{member['id']}", headers=admin).json()["access_token"]
    H = {"Authorization": f"Bearer {access}"}
    back = client.post("/api/auth/stop-impersonation", headers=H)
    assert back.status_code == 200, back.text
    admin_token = back.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert me.json()["id"] == admin_id and me.json()["is_admin"] is True


def test_refresh_preserves_impersonation(client, admin, admin_id, member):
    toks = client.post(f"/api/admin/impersonate/{member['id']}", headers=admin).json()
    refreshed = client.post("/api/auth/refresh", json={"refresh_token": toks["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    new = decode_token(refreshed.json()["access_token"])
    assert new["sub"] == str(member["id"]) and new["act"] == admin_id


def test_only_site_admin_can_impersonate(client, member, admin_id):
    r = client.post(f"/api/admin/impersonate/{admin_id}", headers=member["headers"])
    assert r.status_code == 403


def test_impersonation_guards(client, admin, admin_id):
    assert client.post(f"/api/admin/impersonate/{admin_id}", headers=admin).status_code == 400  # self
    assert client.post("/api/admin/impersonate/999999", headers=admin).status_code == 404       # missing


def test_stop_requires_impersonation_session(client, admin):
    # A normal (non-impersonation) token has no `act` claim.
    assert client.post("/api/auth/stop-impersonation", headers=admin).status_code == 400
