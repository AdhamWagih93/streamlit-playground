"""Entra ID (Azure AD) OIDC auth-code flow via MSAL. Only imported when AUTH_MODE=entra."""
from __future__ import annotations

import json

from ..config import get_settings
from .rbac import User

_SCOPES = ["User.Read"]


def _client():
    import msal  # optional dep (requirements-live.txt)

    s = get_settings()
    return msal.ConfidentialClientApplication(
        s.entra_client_id,
        client_credential=s.entra_client_secret,
        authority=f"https://login.microsoftonline.com/{s.entra_tenant_id}",
    )


def auth_url(state: str) -> str:
    s = get_settings()
    return _client().get_authorization_request_url(
        _SCOPES, state=state, redirect_uri=s.entra_redirect_uri
    )


def redeem_code(code: str) -> User:
    s = get_settings()
    result = _client().acquire_token_by_authorization_code(
        code, scopes=_SCOPES, redirect_uri=s.entra_redirect_uri
    )
    if "id_token_claims" not in result:
        raise ValueError(result.get("error_description", "Entra token redemption failed"))
    claims = result["id_token_claims"]
    groups = claims.get(s.entra_team_claim, []) or []
    role_map = json.loads(s.entra_group_role_map or "{}")
    raw_roles = [role_map[g] for g in groups if g in role_map]
    teams = [g for g in groups if g not in role_map]
    return User(
        username=claims.get("preferred_username", claims.get("oid", "")),
        display_name=claims.get("name", ""),
        email=claims.get("preferred_username", ""),
        raw_roles=raw_roles or ["developer"],
        teams=teams,
    )
