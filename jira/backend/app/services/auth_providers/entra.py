"""Microsoft Entra ID (Azure AD) authentication via the OIDC auth-code flow.

Implemented directly against the v2.0 endpoints and Microsoft Graph using
``httpx`` (no ``msal`` dependency). ``httpx`` is imported lazily so importing
this module never hard-requires it.

The high-level :func:`login` orchestrates token exchange + profile + group
fetch and never raises (returns ``None`` on failure).
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from app.core.crypto import decrypt
from app.models.identity import IdentityProvider

logger = logging.getLogger(__name__)

_AUTHORITY = "https://login.microsoftonline.com"
_GRAPH = "https://graph.microsoft.com/v1.0"
_DEFAULT_SCOPES = "openid profile email User.Read GroupMember.Read.All"
_REQUIRED_SCOPES = ("openid", "profile", "email")
_TIMEOUT = 15.0


def _scopes(provider: IdentityProvider) -> str:
    """Return the effective scope string, always including the OIDC basics."""
    raw = (provider.entra_scopes or "").replace(",", " ").split() if provider.entra_scopes else []
    if not raw:
        raw = _DEFAULT_SCOPES.split()
    # Ensure the required OIDC scopes are present.
    seen = {s.lower() for s in raw}
    for req in _REQUIRED_SCOPES:
        if req not in seen:
            raw.append(req)
            seen.add(req)
    return " ".join(raw)


def authorize_url(provider: IdentityProvider, state: str) -> str:
    """Build the authorization-request URL the browser is redirected to."""
    tenant = provider.entra_tenant_id or "common"
    params = {
        "client_id": provider.entra_client_id or "",
        "response_type": "code",
        "redirect_uri": provider.entra_redirect_uri or "",
        "response_mode": "query",
        "scope": _scopes(provider),
        "state": state,
    }
    return f"{_AUTHORITY}/{tenant}/oauth2/v2.0/authorize?{urlencode(params)}"


def exchange_code(provider: IdentityProvider, code: str) -> dict:
    """Exchange an authorization *code* for tokens. Returns the token JSON."""
    import httpx

    tenant = provider.entra_tenant_id or "common"
    token_url = f"{_AUTHORITY}/{tenant}/oauth2/v2.0/token"
    data = {
        "client_id": provider.entra_client_id or "",
        "client_secret": decrypt(provider.entra_client_secret_enc) or "",
        "code": code,
        "redirect_uri": provider.entra_redirect_uri or "",
        "grant_type": "authorization_code",
        "scope": _scopes(provider),
    }
    resp = httpx.post(token_url, data=data, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_profile(access_token: str) -> dict:
    """Fetch the signed-in user's Graph profile."""
    import httpx

    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"$select": "id,userPrincipalName,mail,displayName"}
    resp = httpx.get(f"{_GRAPH}/me", headers=headers, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_groups(access_token: str) -> list[str]:
    """Fetch the user's group display names. Tolerates 403 (missing scope)."""
    import httpx

    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{_GRAPH}/me/memberOf?$select=displayName"
    names: list[str] = []
    try:
        while url:
            resp = httpx.get(url, headers=headers, timeout=_TIMEOUT)
            if resp.status_code == 403:
                logger.info("Entra group fetch: insufficient scope (403); skipping groups")
                return []
            resp.raise_for_status()
            payload = resp.json()
            for item in payload.get("value", []):
                name = item.get("displayName")
                if name:
                    names.append(str(name))
            url = payload.get("@odata.nextLink")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Entra group fetch failed: %s", exc)
        return names
    return names


def login(provider: IdentityProvider, code: str) -> dict | None:
    """Orchestrate exchange + profile + groups.

    Returns ``{"external_id", "username", "email", "display_name", "groups"}``
    on success or ``None`` on failure. Never raises.
    """
    try:
        token = exchange_code(provider, code)
        access_token = token.get("access_token")
        if not access_token:
            logger.warning("Entra token exchange returned no access_token: %s", token.get("error"))
            return None
        profile = fetch_profile(access_token)
        groups = fetch_groups(access_token) if provider.sync_groups else []
        username = profile.get("userPrincipalName") or profile.get("mail") or profile.get("id")
        email = profile.get("mail") or profile.get("userPrincipalName")
        return {
            "external_id": profile.get("id"),
            "username": username,
            "email": email,
            "display_name": profile.get("displayName") or username,
            "groups": groups,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Entra login failed: %s", exc)
        return None
