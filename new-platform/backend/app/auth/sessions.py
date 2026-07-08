"""Backend-issued session JWT (httpOnly cookie). Same shape for every auth mode."""
from __future__ import annotations

import time
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, Response

from ..config import get_settings
from .rbac import User

ALGO = "HS256"


def issue_session(response: Response, user: User) -> None:
    s = get_settings()
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": user.username,
            "name": user.display_name,
            "email": user.email,
            "raw_roles": user.raw_roles,
            "teams": user.teams,
            "iat": now,
            "exp": now + s.session_ttl_hours * 3600,
        },
        s.session_secret,
        algorithm=ALGO,
    )
    response.set_cookie(
        s.cookie_name, token,
        httponly=True, samesite="lax", secure=s.cookie_secure,
        max_age=s.session_ttl_hours * 3600, path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(get_settings().cookie_name, path="/")


def _decode(token: str) -> Optional[User]:
    s = get_settings()
    try:
        p = jwt.decode(token, s.session_secret, algorithms=[ALGO])
    except jwt.PyJWTError:
        return None
    return User(
        username=p.get("sub", ""),
        display_name=p.get("name", ""),
        email=p.get("email", ""),
        raw_roles=list(p.get("raw_roles", [])),
        teams=list(p.get("teams", [])),
    )


def _dev_user() -> User:
    s = get_settings()
    return User(
        username=s.dev_username,
        display_name=s.dev_display_name,
        email=s.dev_email,
        raw_roles=[r.strip() for r in s.dev_roles.split(",") if r.strip()],
        teams=[t.strip() for t in s.dev_teams.split(",") if t.strip()],
    )


def current_user(request: Request) -> User:
    s = get_settings()
    token = request.cookies.get(s.cookie_name, "")
    user = _decode(token) if token else None
    if user is None:
        if s.auth_mode == "none":
            # Local dev: auto-login. A previously issued dev-switch cookie (decoded above)
            # takes precedence so the role switcher persists across requests.
            return _dev_user()
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
