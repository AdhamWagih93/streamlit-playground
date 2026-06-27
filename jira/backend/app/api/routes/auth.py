"""Authentication routes: login, register, token refresh and self-profile.

Also exposes external-provider discovery and the Microsoft Entra ID OIDC
authorization-code flow, and augments local login with an LDAP fallback.
"""
from __future__ import annotations

import secrets
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, oauth2_scheme
from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import IdentityProvider, User
from app.schemas.admin import AuthPolicyPublic
from app.schemas.auth import RefreshRequest, RegisterRequest, Token
from app.schemas.user import UserOut, UserUpdate
from app.services import auth_settings as auth_settings_service
from app.services.auth_providers import directory
from app.services.auth_providers import entra as entra_provider
from app.services.auth_providers import ldap as ldap_provider

router = APIRouter()


class ProviderOut(BaseModel):
    id: int
    name: str
    type: str
    enabled: bool


def _frontend_base() -> str:
    """Best-effort SPA origin for post-login redirects."""
    origins = settings.cors_origins or []
    return origins[0] if origins else "/"

_CRED_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
    headers={"WWW-Authenticate": "Bearer"},
)


@router.post("/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> Token:
    """OAuth2 password flow. ``form.username`` is treated as the user's email.

    Tries a local password first; on any local failure it falls back to each
    enabled LDAP provider (JIT-provisioning the user on success).
    """
    access_min = auth_settings_service.access_token_minutes(db)
    refresh_min = auth_settings_service.refresh_token_minutes(db)

    # Local password login is honoured only when the admin allows it.
    if auth_settings_service.local_login_allowed(db):
        user = db.scalars(select(User).where(User.email == form.username)).first()
        if (
            user is not None
            and user.is_active
            and user.auth_source == "local"
            and verify_password(form.password, user.password_hash)
        ):
            return Token(
                access_token=create_access_token(user.id, minutes=access_min),
                refresh_token=create_refresh_token(user.id, minutes=refresh_min),
            )

    # Local auth failed/disabled. Fall back to each enabled LDAP provider using
    # the supplied credentials (JIT-provisioning on success).
    for provider in directory.find_enabled(db, "ldap"):
        info = ldap_provider.authenticate(provider, form.username, form.password)
        if not info:
            continue
        try:
            provisioned = directory.provision_user(db, provider, info)
        except PermissionError:
            continue
        if not provisioned.is_active:
            continue
        tokens = directory.issue_tokens(provisioned, access_min, refresh_min)
        return Token(**tokens)

    raise _CRED_EXC


@router.get("/policy", response_model=AuthPolicyPublic)
def auth_policy(db: Session = Depends(get_db)) -> AuthPolicyPublic:
    """Public auth policy so the login/register screens can adapt the UI."""
    s = auth_settings_service.get_auth_settings(db)
    return AuthPolicyPublic(
        allow_local_login=s.allow_local_login,
        allow_self_registration=s.allow_self_registration,
    )


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(db: Session = Depends(get_db)) -> list[ProviderOut]:
    """Public list of enabled identity providers for the login page."""
    providers = db.scalars(
        select(IdentityProvider)
        .where(IdentityProvider.enabled.is_(True))
        .order_by(IdentityProvider.order.asc(), IdentityProvider.id.asc())
    ).all()
    return [
        ProviderOut(id=p.id, name=p.name, type=p.provider_type, enabled=p.enabled)
        for p in providers
    ]


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    if not auth_settings_service.self_registration_allowed(db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-registration is disabled by the administrator",
        )
    if not auth_settings_service.registration_email_allowed(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That email domain is not permitted to self-register",
        )
    existing = db.scalars(
        select(User).where(
            or_(User.email == payload.email, User.username == payload.username)
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that email or username already exists",
        )
    user = User(
        username=payload.username,
        email=payload.email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/refresh", response_model=Token)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> Token:
    try:
        data = decode_token(payload.refresh_token)
        if data.get("type") != "refresh":
            raise _CRED_EXC
        user_id = int(data["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _CRED_EXC
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _CRED_EXC
    # Preserve an active impersonation session across refresh.
    extra = {"act": data["act"], "imp": True} if data.get("act") is not None else None
    return Token(
        access_token=create_access_token(user.id, extra=extra, minutes=auth_settings_service.access_token_minutes(db)),
        refresh_token=create_refresh_token(user.id, extra=extra, minutes=auth_settings_service.refresh_token_minutes(db)),
    )


@router.post("/stop-impersonation", response_model=Token)
def stop_impersonation(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Token:
    """End an impersonation session and return to the real (admin) account,
    using the ``act`` claim recorded in the current token."""
    if not token:
        raise _CRED_EXC
    try:
        data = decode_token(token)
        actor_id = data.get("act")
        if actor_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not an impersonation session")
        admin = db.get(User, int(actor_id))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _CRED_EXC
    if admin is None or not admin.is_active:
        raise _CRED_EXC
    return Token(
        access_token=create_access_token(admin.id, minutes=auth_settings_service.access_token_minutes(db)),
        refresh_token=create_refresh_token(admin.id, minutes=auth_settings_service.refresh_token_minutes(db)),
    )


def _get_entra_provider(db: Session, provider_id: int) -> IdentityProvider:
    provider = db.get(IdentityProvider, provider_id)
    if provider is None or not provider.enabled or provider.provider_type != "entra":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    return provider


@router.get("/entra/{provider_id}/authorize")
def entra_authorize(provider_id: int, db: Session = Depends(get_db)) -> dict:
    """Return the Entra authorization URL the browser should be redirected to."""
    provider = _get_entra_provider(db, provider_id)
    # Simple state: provider id + a random nonce (CSRF/replay mitigation).
    state = f"{provider_id}.{secrets.token_urlsafe(16)}"
    return {"authorization_url": entra_provider.authorize_url(provider, state)}


@router.get("/entra/{provider_id}/callback")
def entra_callback(
    provider_id: int,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Complete the OIDC flow and redirect to the SPA with tokens in the fragment."""
    base = _frontend_base().rstrip("/")
    target = f"{base}/auth/callback"

    def _fail(msg: str) -> RedirectResponse:
        return RedirectResponse(url=f"{target}#error={quote(msg)}", status_code=302)

    if error:
        return _fail(error)
    if not code:
        return _fail("missing_code")

    provider = db.get(IdentityProvider, provider_id)
    if provider is None or not provider.enabled or provider.provider_type != "entra":
        return _fail("provider_not_found")

    info = entra_provider.login(provider, code)
    if not info:
        return _fail("authentication_failed")

    try:
        user = directory.provision_user(db, provider, info)
    except PermissionError:
        return _fail("provisioning_disabled")
    if not user.is_active:
        return _fail("account_disabled")

    tokens = directory.issue_tokens(
        user,
        auth_settings_service.access_token_minutes(db),
        auth_settings_service.refresh_token_minutes(db),
    )
    fragment = (
        f"#access_token={quote(tokens['access_token'])}"
        f"&refresh_token={quote(tokens['refresh_token'])}"
    )
    return RedirectResponse(url=f"{target}{fragment}", status_code=302)


@router.get("/me", response_model=UserOut)
def read_me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Self-service profile update. Privilege fields are intentionally ignored."""
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url
    if payload.timezone is not None:
        user.timezone = payload.timezone
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
