from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..config import get_settings
from .rbac import User
from .sessions import clear_session, current_user, issue_session

router = APIRouter(prefix="/auth", tags=["auth"])
_entra_states: set[str] = set()


@router.get("/me")
def me(user: User = Depends(current_user)):
    s = get_settings()
    return {
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "roles": user.roles,
        "role": user.role,
        "is_admin": user.is_admin,
        "teams": user.teams,
        "visible_envs": user.visible_envs,
        "visible_event_types": user.visible_event_types,
        "auth_mode": s.auth_mode,
        "data_mode": s.data_mode,
    }


class LdapLogin(BaseModel):
    username: str
    password: str


@router.post("/login")
def ldap_login(body: LdapLogin, response: Response):
    s = get_settings()
    if s.auth_mode != "ldap":
        raise HTTPException(status_code=400, detail=f"Password login disabled (AUTH_MODE={s.auth_mode})")
    from .ldap_auth import authenticate
    try:
        user = authenticate(body.username, body.password)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    issue_session(response, user)
    return {"ok": True, "role": user.role}


@router.get("/entra/login")
def entra_login():
    s = get_settings()
    if s.auth_mode != "entra":
        raise HTTPException(status_code=400, detail=f"Entra login disabled (AUTH_MODE={s.auth_mode})")
    from .entra import auth_url
    state = secrets.token_urlsafe(24)
    _entra_states.add(state)
    return RedirectResponse(auth_url(state))


@router.get("/entra/callback")
def entra_callback(request: Request, code: str = "", state: str = ""):
    s = get_settings()
    if s.auth_mode != "entra":
        raise HTTPException(status_code=400, detail="Entra disabled")
    if state not in _entra_states:
        raise HTTPException(status_code=400, detail="Bad OIDC state")
    _entra_states.discard(state)
    from .entra import redeem_code
    try:
        user = redeem_code(code)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Entra sign-in failed: {exc}")
    resp = RedirectResponse("/")
    issue_session(resp, user)
    return resp


@router.post("/logout")
def logout(response: Response):
    clear_session(response)
    return {"ok": True}


class DevSwitch(BaseModel):
    roles: list[str]
    teams: list[str]


@router.post("/dev/switch")
def dev_switch(body: DevSwitch, response: Response):
    """AUTH_MODE=none only: preview the app as any role/team combination."""
    s = get_settings()
    if s.auth_mode != "none":
        raise HTTPException(status_code=403, detail="Role switching is a dev-mode feature")
    user = User(
        username=s.dev_username, display_name=s.dev_display_name, email=s.dev_email,
        raw_roles=body.roles or ["developer"], teams=body.teams,
    )
    issue_session(response, user)
    return {"ok": True, "role": user.role, "teams": user.teams}
