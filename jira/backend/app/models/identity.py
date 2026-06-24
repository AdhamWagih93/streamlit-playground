"""Instance configuration managed through the admin UI.

These rows hold connection/config for cross-cutting infrastructure: the
outbound mail server, Jira connections used for import/sync, and external
identity providers (LDAP, Microsoft Entra ID). Secret fields are stored
encrypted via app.core.crypto and are never returned to clients in clear text.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class AuthSettings(Base, TimestampMixin):
    """Instance-wide authentication policy (singleton row, id always 1).

    Lets instance admins control login/registration behaviour and token
    lifetimes from the UI instead of environment variables.
    """

    __tablename__ = "auth_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Allow username/password login against local accounts. When false, only
    # configured directory/SSO providers can authenticate.
    allow_local_login: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Allow anonymous users to self-register a local account.
    allow_self_registration: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Token lifetimes (minutes). Null => fall back to the env default.
    access_token_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refresh_token_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Optional comma-separated email-domain allowlist for self-registration.
    registration_allowed_domains: Mapped[str | None] = mapped_column(Text, nullable=True)


class MailConfig(Base, TimestampMixin):
    """Singleton-ish outbound SMTP configuration (row id is always 1)."""

    __tablename__ = "mail_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    use_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # STARTTLS
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # implicit TLS
    from_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_name: Mapped[str] = mapped_column(String(255), default="Trackly", nullable=False)


class JiraConnection(Base, TimestampMixin):
    """A configured Jira instance Trackly can import/sync from."""

    __tablename__ = "jira_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    # cloud (Basic email+token) | server (Bearer PAT)
    auth_mode: Mapped[str] = mapped_column(String(20), default="cloud", nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Last successful connectivity check, for the admin UI status badge.
    last_checked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class IdentityProvider(Base, TimestampMixin):
    """An external authentication/authorization source (LDAP or Entra ID)."""

    __tablename__ = "identity_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # ldap | entra
    provider_type: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Whether to auto-create (JIT) users on first successful login.
    auto_provision_users: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Whether to sync the provider's groups onto Trackly groups for authz.
    sync_groups: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- LDAP fields (used when provider_type == 'ldap') ---
    ldap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ldap_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ldap_use_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ldap_bind_dn: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ldap_bind_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    ldap_user_base_dn: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ldap_user_filter: Mapped[str | None] = mapped_column(String(512), nullable=True)  # e.g. (uid={username})
    ldap_attr_username: Mapped[str] = mapped_column(String(64), default="uid", nullable=False)
    ldap_attr_email: Mapped[str] = mapped_column(String(64), default="mail", nullable=False)
    ldap_attr_display_name: Mapped[str] = mapped_column(String(64), default="cn", nullable=False)
    ldap_group_base_dn: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ldap_group_filter: Mapped[str | None] = mapped_column(String(512), nullable=True)  # e.g. (member={user_dn})
    ldap_attr_group_name: Mapped[str] = mapped_column(String(64), default="cn", nullable=False)

    # --- Entra ID / OIDC fields (used when provider_type == 'entra') ---
    entra_tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entra_client_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entra_client_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    entra_redirect_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # space/comma separated extra scopes; defaults applied in the provider.
    entra_scopes: Mapped[str | None] = mapped_column(String(512), nullable=True)
