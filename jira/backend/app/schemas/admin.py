"""Schemas for instance settings: mail, Jira connections, identity providers.

Secret fields are write-only: clients send them to set/update, but read models
never echo them back. A boolean ``*_set`` flag tells the UI whether a secret is
currently stored.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ORMModel


# --- Mail ------------------------------------------------------------------
class MailConfigOut(ORMModel):
    enabled: bool
    host: str | None = None
    port: int = 587
    username: str | None = None
    use_tls: bool = True
    use_ssl: bool = False
    from_address: str | None = None
    from_name: str = "Trackly"
    password_set: bool = False


class MailConfigIn(BaseModel):
    enabled: bool = False
    host: str | None = None
    port: int = 587
    username: str | None = None
    password: str | None = None  # write-only; omit to keep existing
    use_tls: bool = True
    use_ssl: bool = False
    from_address: str | None = None
    from_name: str = "Trackly"


class TestEmailIn(BaseModel):
    to: str


# --- Authentication policy -------------------------------------------------
class AuthSettingsOut(ORMModel):
    allow_local_login: bool = True
    allow_self_registration: bool = True
    access_token_minutes: int | None = None
    refresh_token_minutes: int | None = None
    registration_allowed_domains: str | None = None


class AuthSettingsIn(BaseModel):
    allow_local_login: bool = True
    allow_self_registration: bool = True
    access_token_minutes: int | None = None
    refresh_token_minutes: int | None = None
    registration_allowed_domains: str | None = None


class AuthPolicyPublic(BaseModel):
    """Public subset of the auth policy used by the login/register screens."""

    allow_local_login: bool
    allow_self_registration: bool


# --- Jira connections ------------------------------------------------------
class JiraConnectionOut(ORMModel):
    id: int
    name: str
    base_url: str
    auth_mode: str
    email: str | None = None
    verify_ssl: bool
    enabled: bool
    is_default: bool
    last_checked_at: str | None = None
    last_check_ok: bool | None = None
    token_set: bool = False


class JiraConnectionIn(BaseModel):
    name: str
    base_url: str
    auth_mode: str = "cloud"  # cloud | server
    email: str | None = None
    api_token: str | None = None  # write-only
    verify_ssl: bool = True
    enabled: bool = True
    is_default: bool = False


class JiraConnectionUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    auth_mode: str | None = None
    email: str | None = None
    api_token: str | None = None
    verify_ssl: bool | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class ConnectionTestResult(BaseModel):
    ok: bool
    message: str
    account: str | None = None


class JiraProjectSummary(BaseModel):
    key: str
    name: str
    id: str | None = None
    project_type: str | None = None
    lead: str | None = None
    # Whether a Trackly project with this key already exists locally.
    exists_locally: bool = False


# --- Identity providers (LDAP / Entra) -------------------------------------
class IdentityProviderOut(ORMModel):
    id: int
    name: str
    provider_type: str
    enabled: bool
    auto_provision_users: bool
    sync_groups: bool
    order: int
    # LDAP (non-secret)
    ldap_host: str | None = None
    ldap_port: int | None = None
    ldap_use_ssl: bool = True
    ldap_bind_dn: str | None = None
    ldap_user_base_dn: str | None = None
    ldap_user_filter: str | None = None
    ldap_attr_username: str = "uid"
    ldap_attr_email: str = "mail"
    ldap_attr_display_name: str = "cn"
    ldap_group_base_dn: str | None = None
    ldap_group_filter: str | None = None
    ldap_attr_group_name: str = "cn"
    ldap_bind_password_set: bool = False
    # Entra (non-secret)
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_redirect_uri: str | None = None
    entra_scopes: str | None = None
    entra_client_secret_set: bool = False


class IdentityProviderIn(BaseModel):
    name: str
    provider_type: str  # ldap | entra
    enabled: bool = False
    auto_provision_users: bool = True
    sync_groups: bool = True
    order: int = 0
    # LDAP
    ldap_host: str | None = None
    ldap_port: int | None = None
    ldap_use_ssl: bool = True
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None  # write-only
    ldap_user_base_dn: str | None = None
    ldap_user_filter: str | None = None
    ldap_attr_username: str = "uid"
    ldap_attr_email: str = "mail"
    ldap_attr_display_name: str = "cn"
    ldap_group_base_dn: str | None = None
    ldap_group_filter: str | None = None
    ldap_attr_group_name: str = "cn"
    # Entra
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: str | None = None  # write-only
    entra_redirect_uri: str | None = None
    entra_scopes: str | None = None


class IdentityProviderUpdate(IdentityProviderIn):
    # All optional on update.
    name: str | None = None
    provider_type: str | None = None
    enabled: bool | None = None
    auto_provision_users: bool | None = None
    sync_groups: bool | None = None
