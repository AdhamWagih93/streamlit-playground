"""Central configuration. Everything is env-driven so the same image runs
locally (demo/no-auth) and deployed (live/entra|ldap) with no code changes."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- modes -------------------------------------------------------------
    data_mode: Literal["demo", "live"] = "demo"
    auth_mode: Literal["none", "entra", "ldap"] = "none"
    # Containers must not silently run open: `none` inside a container requires this.
    allow_insecure_no_auth: bool = False
    running_in_container: bool = False  # set by Dockerfile

    # ---- session -----------------------------------------------------------
    session_secret: str = "dev-only-secret-change-me-for-any-real-deployment"
    session_ttl_hours: int = 12
    cookie_secure: bool = False  # set true behind TLS
    cookie_name: str = "meridian_session"

    # ---- dev auto-login (AUTH_MODE=none) ------------------------------------
    dev_username: str = "adham"
    dev_display_name: str = "Adham Meshhal"
    dev_email: str = "adham.meshhal@example.com"
    dev_roles: str = "admin"          # comma-separated raw role strings
    dev_teams: str = "Platform"       # comma-separated

    # ---- Entra ID (OIDC auth-code) ------------------------------------------
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""
    entra_redirect_uri: str = ""      # e.g. https://host/api/auth/entra/callback
    entra_group_role_map: str = ""    # JSON: {"<group-id-or-name>": "Admin", ...}
    entra_team_claim: str = "groups"

    # ---- LDAP auth -----------------------------------------------------------
    ldap_url: str = ""                # ldaps://dc.example.com
    ldap_bind_dn_template: str = ""   # e.g. "{username}@corp.example.com" or full DN template
    ldap_user_search_base: str = ""
    ldap_user_filter: str = "(sAMAccountName={username})"
    ldap_group_role_map: str = ""     # JSON: {"CN=devops-admins,...": "Admin", ...}
    ldap_service_bind_dn: str = ""    # optional service account for lookups
    ldap_service_password: str = ""

    # ---- platform database (the ONLY external integration configured via env) --
    # e.g. postgresql://meridian:secret@localhost:5433/meridian
    database_url: str = ""
    # Fernet key (32-byte urlsafe base64) encrypting integration configs at rest.
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Empty → key derived from session_secret (dev only; the UI warns).
    settings_encryption_key: str = ""

    # ---- legacy env fallbacks (runtime integrations now live in the in-app
    #      Settings page, encrypted in Postgres; these remain only as defaults) ----
    vault_addr: str = ""
    vault_token: str = ""
    vault_token_file: str = ""        # mounted secret file wins over env token
    git_vault_path: str = "new_git"
    jenkins_vault_path: str = "jenkins"
    postgres_vault_path: str = "postgres"
    prisma_s3_vault_path: str = "s3"
    ado_vault_path: str = "ado"
    es_hosts: str = ""                # comma-separated
    es_api_key: str = ""
    es_verify_certs: bool = False
    cicd_repo_base: str = "/tmp/meridian-repos"
    inventory_repo_url_template: str = "http://{host}/DevOps/Platform/_git/inventories"
    config_repo_url_template: str = "http://{host}/DevOps/Control/_git/{repo}"
    config_branch: str = "main"
    prisma_s3_bucket: str = "PrismaCloud-Logs"
    prisma_s3_key_pattern: str = "{project}/{application}_{version}-PrismaCloudLog.txt"
    postgres_table: str = "devops_projects"
    ado_api_version: str = "6.0"
    docchat_ollama_url: str = "http://ef-nexus-03:8081"
    docchat_model: str = "qwen3.5:9b"

    # ---- demo world ----------------------------------------------------------
    demo_seed: int = 20260703

    # ---- server ---------------------------------------------------------------
    frontend_dist: str = ""           # when set, serve the built SPA from here
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    def validate_runtime(self) -> None:
        if self.auth_mode == "none" and self.running_in_container and not self.allow_insecure_no_auth:
            raise RuntimeError(
                "AUTH_MODE=none inside a container is refused. Configure AUTH_MODE=entra or "
                "AUTH_MODE=ldap, or explicitly set ALLOW_INSECURE_NO_AUTH=true."
            )
        if self.auth_mode == "entra":
            missing = [k for k in ("entra_tenant_id", "entra_client_id", "entra_client_secret",
                                   "entra_redirect_uri") if not getattr(self, k)]
            if missing:
                raise RuntimeError(f"AUTH_MODE=entra requires: {', '.join(m.upper() for m in missing)}")
        if self.auth_mode == "ldap" and not (self.ldap_url and self.ldap_bind_dn_template):
            raise RuntimeError("AUTH_MODE=ldap requires LDAP_URL and LDAP_BIND_DN_TEMPLATE")


@lru_cache
def get_settings() -> Settings:
    return Settings()
