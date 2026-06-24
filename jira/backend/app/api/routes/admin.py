"""Instance administration: mail, Jira connections, identity providers, and
global permissions. Every route requires site-administrator privileges.

Mounted at ``/api/admin``. Secret fields are write-only — they are stored
encrypted and never echoed back; read models expose ``*_set`` booleans instead.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import encrypt, decrypt
from app.core.database import get_db
from app.models import (
    GlobalPermissionGrant,
    IdentityProvider,
    JiraConnection,
    MailConfig,
    Project,
    User,
)
from app.migration.jira_client import JiraClient
from app.schemas.admin import (
    AuthSettingsIn,
    AuthSettingsOut,
    ConnectionTestResult,
    IdentityProviderIn,
    IdentityProviderOut,
    IdentityProviderUpdate,
    JiraConnectionIn,
    JiraConnectionOut,
    JiraConnectionUpdate,
    JiraProjectSummary,
    MailConfigIn,
    MailConfigOut,
    TestEmailIn,
)
from app.schemas.common import Message
from app.schemas.rbac import GlobalGrantIn, GlobalGrantOut
from app.services import auth_settings as auth_settings_service
from app.services import mail as mail_service
from app.services import permission_keys as P
from app.services.permissions import require_site_admin

router = APIRouter()


# --- Authentication policy -------------------------------------------------
@router.get("/auth-settings", response_model=AuthSettingsOut)
def get_auth_settings(
    db: Session = Depends(get_db), _admin: User = Depends(require_site_admin)
) -> AuthSettingsOut:
    return AuthSettingsOut.model_validate(auth_settings_service.get_auth_settings(db))


@router.put("/auth-settings", response_model=AuthSettingsOut)
def update_auth_settings(
    payload: AuthSettingsIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> AuthSettingsOut:
    row = auth_settings_service.get_auth_settings(db)
    row.allow_local_login = payload.allow_local_login
    row.allow_self_registration = payload.allow_self_registration
    row.access_token_minutes = payload.access_token_minutes
    row.refresh_token_minutes = payload.refresh_token_minutes
    row.registration_allowed_domains = payload.registration_allowed_domains
    db.commit()
    db.refresh(row)
    return AuthSettingsOut.model_validate(row)


# --- helpers ---------------------------------------------------------------
def _apply_secret(current_enc: str | None, incoming: str | None) -> str | None:
    """Resolve a write-only secret field per the keep/replace/clear rule.

    - ``incoming is None`` (omitted) -> keep the existing encrypted value.
    - ``incoming == ""`` -> clear the secret (return None).
    - non-empty string -> (re-)encrypt and store.
    """
    if incoming is None:
        return current_enc
    if incoming == "":
        return None
    return encrypt(incoming)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# MAIL
# ===========================================================================
def _mail_out(cfg: MailConfig) -> MailConfigOut:
    return MailConfigOut(
        enabled=cfg.enabled,
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        use_tls=cfg.use_tls,
        use_ssl=cfg.use_ssl,
        from_address=cfg.from_address,
        from_name=cfg.from_name,
        password_set=bool(cfg.password_enc),
    )


def _get_or_create_mail(db: Session) -> MailConfig:
    cfg = db.get(MailConfig, 1)
    if cfg is None:
        cfg = MailConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@router.get("/mail", response_model=MailConfigOut)
def get_mail(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> MailConfigOut:
    cfg = _get_or_create_mail(db)
    return _mail_out(cfg)


@router.put("/mail", response_model=MailConfigOut)
def update_mail(
    payload: MailConfigIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> MailConfigOut:
    cfg = _get_or_create_mail(db)
    cfg.enabled = payload.enabled
    cfg.host = payload.host
    cfg.port = payload.port
    cfg.username = payload.username
    cfg.use_tls = payload.use_tls
    cfg.use_ssl = payload.use_ssl
    cfg.from_address = payload.from_address
    cfg.from_name = payload.from_name
    cfg.password_enc = _apply_secret(cfg.password_enc, payload.password)
    db.commit()
    db.refresh(cfg)
    return _mail_out(cfg)


@router.post("/mail/test", response_model=ConnectionTestResult)
def test_mail(
    payload: TestEmailIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> ConnectionTestResult:
    ok, msg = mail_service.send_test_email(db, payload.to)
    return ConnectionTestResult(ok=ok, message=msg)


# ===========================================================================
# JIRA CONNECTIONS
# ===========================================================================
def _jira_out(conn: JiraConnection) -> JiraConnectionOut:
    return JiraConnectionOut(
        id=conn.id,
        name=conn.name,
        base_url=conn.base_url,
        auth_mode=conn.auth_mode,
        email=conn.email,
        verify_ssl=conn.verify_ssl,
        enabled=conn.enabled,
        is_default=conn.is_default,
        last_checked_at=conn.last_checked_at,
        last_check_ok=conn.last_check_ok,
        token_set=bool(conn.api_token_enc),
    )


def _clear_other_defaults(db: Session, keep_id: int | None) -> None:
    others = db.scalars(
        select(JiraConnection).where(JiraConnection.is_default.is_(True))
    ).all()
    for other in others:
        if keep_id is None or other.id != keep_id:
            other.is_default = False


def _build_jira_client(conn: JiraConnection) -> JiraClient:
    token = decrypt(conn.api_token_enc) or ""
    if conn.auth_mode == "server":
        return JiraClient(
            base_url=conn.base_url,
            email="",
            api_token=token,
            verify=conn.verify_ssl,
            server_token=True,
        )
    return JiraClient(
        base_url=conn.base_url,
        email=conn.email or "",
        api_token=token,
        verify=conn.verify_ssl,
    )


@router.get("/jira-connections", response_model=list[JiraConnectionOut])
def list_jira_connections(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> list[JiraConnectionOut]:
    conns = db.scalars(select(JiraConnection).order_by(JiraConnection.id)).all()
    return [_jira_out(c) for c in conns]


@router.post("/jira-connections", response_model=JiraConnectionOut, status_code=status.HTTP_201_CREATED)
def create_jira_connection(
    payload: JiraConnectionIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> JiraConnectionOut:
    conn = JiraConnection(
        name=payload.name,
        base_url=payload.base_url,
        auth_mode=payload.auth_mode,
        email=payload.email,
        api_token_enc=encrypt(payload.api_token) if payload.api_token else None,
        verify_ssl=payload.verify_ssl,
        enabled=payload.enabled,
        is_default=payload.is_default,
    )
    db.add(conn)
    db.flush()
    if conn.is_default:
        _clear_other_defaults(db, keep_id=conn.id)
    db.commit()
    db.refresh(conn)
    return _jira_out(conn)


@router.patch("/jira-connections/{conn_id}", response_model=JiraConnectionOut)
def update_jira_connection(
    conn_id: int,
    payload: JiraConnectionUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> JiraConnectionOut:
    conn = db.get(JiraConnection, conn_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    if payload.name is not None:
        conn.name = payload.name
    if payload.base_url is not None:
        conn.base_url = payload.base_url
    if payload.auth_mode is not None:
        conn.auth_mode = payload.auth_mode
    if payload.email is not None:
        conn.email = payload.email
    if payload.verify_ssl is not None:
        conn.verify_ssl = payload.verify_ssl
    if payload.enabled is not None:
        conn.enabled = payload.enabled

    conn.api_token_enc = _apply_secret(conn.api_token_enc, payload.api_token)

    if payload.is_default is not None:
        conn.is_default = payload.is_default
        if payload.is_default:
            _clear_other_defaults(db, keep_id=conn.id)

    db.commit()
    db.refresh(conn)
    return _jira_out(conn)


@router.delete("/jira-connections/{conn_id}", response_model=Message)
def delete_jira_connection(
    conn_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> Message:
    conn = db.get(JiraConnection, conn_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    db.delete(conn)
    db.commit()
    return Message(detail="Connection deleted")


@router.post("/jira-connections/{conn_id}/test", response_model=ConnectionTestResult)
def test_jira_connection(
    conn_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> ConnectionTestResult:
    conn = db.get(JiraConnection, conn_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    ok = False
    message = ""
    account: str | None = None
    try:
        client = _build_jira_client(conn)
        try:
            me = client.get_myself()
        finally:
            client.close()
        account = (
            me.get("displayName")
            or me.get("name")
            or me.get("emailAddress")
            or me.get("accountId")
        )
        ok = True
        message = "Connection successful"
    except Exception as exc:  # noqa: BLE001 - never 500 on a bad remote
        ok = False
        message = str(exc) or "Connection failed"

    conn.last_checked_at = _now_iso()
    conn.last_check_ok = ok
    db.commit()

    return ConnectionTestResult(ok=ok, message=message, account=account)


@router.get("/jira-connections/{conn_id}/projects", response_model=list[JiraProjectSummary])
def list_jira_projects(
    conn_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> list[JiraProjectSummary]:
    conn = db.get(JiraConnection, conn_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    local_keys = {
        k.upper() for k in db.scalars(select(Project.key)).all() if k
    }

    summaries: list[JiraProjectSummary] = []
    try:
        client = _build_jira_client(conn)
        try:
            for proj in client.iter_projects():
                key = proj.get("key") or ""
                lead = proj.get("lead") or {}
                lead_name = None
                if isinstance(lead, dict):
                    lead_name = lead.get("displayName") or lead.get("name")
                summaries.append(
                    JiraProjectSummary(
                        key=key,
                        name=proj.get("name") or key,
                        id=str(proj["id"]) if proj.get("id") is not None else None,
                        project_type=proj.get("projectTypeKey"),
                        lead=lead_name,
                        exists_locally=key.upper() in local_keys,
                    )
                )
                if len(summaries) >= 500:
                    break
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Failed to list projects",
        )

    return summaries


# ===========================================================================
# IDENTITY PROVIDERS
# ===========================================================================
def _idp_out(idp: IdentityProvider) -> IdentityProviderOut:
    return IdentityProviderOut(
        id=idp.id,
        name=idp.name,
        provider_type=idp.provider_type,
        enabled=idp.enabled,
        auto_provision_users=idp.auto_provision_users,
        sync_groups=idp.sync_groups,
        order=idp.order,
        ldap_host=idp.ldap_host,
        ldap_port=idp.ldap_port,
        ldap_use_ssl=idp.ldap_use_ssl,
        ldap_bind_dn=idp.ldap_bind_dn,
        ldap_user_base_dn=idp.ldap_user_base_dn,
        ldap_user_filter=idp.ldap_user_filter,
        ldap_attr_username=idp.ldap_attr_username,
        ldap_attr_email=idp.ldap_attr_email,
        ldap_attr_display_name=idp.ldap_attr_display_name,
        ldap_group_base_dn=idp.ldap_group_base_dn,
        ldap_group_filter=idp.ldap_group_filter,
        ldap_attr_group_name=idp.ldap_attr_group_name,
        ldap_bind_password_set=bool(idp.ldap_bind_password_enc),
        entra_tenant_id=idp.entra_tenant_id,
        entra_client_id=idp.entra_client_id,
        entra_redirect_uri=idp.entra_redirect_uri,
        entra_scopes=idp.entra_scopes,
        entra_client_secret_set=bool(idp.entra_client_secret_enc),
    )


# Non-secret LDAP/Entra scalar fields copied verbatim from the payload.
_IDP_PLAIN_FIELDS = (
    "ldap_host",
    "ldap_port",
    "ldap_use_ssl",
    "ldap_bind_dn",
    "ldap_user_base_dn",
    "ldap_user_filter",
    "ldap_attr_username",
    "ldap_attr_email",
    "ldap_attr_display_name",
    "ldap_group_base_dn",
    "ldap_group_filter",
    "ldap_attr_group_name",
    "entra_tenant_id",
    "entra_client_id",
    "entra_redirect_uri",
    "entra_scopes",
)


@router.get("/identity-providers", response_model=list[IdentityProviderOut])
def list_identity_providers(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> list[IdentityProviderOut]:
    idps = db.scalars(
        select(IdentityProvider).order_by(IdentityProvider.order, IdentityProvider.id)
    ).all()
    return [_idp_out(i) for i in idps]


@router.post("/identity-providers", response_model=IdentityProviderOut, status_code=status.HTTP_201_CREATED)
def create_identity_provider(
    payload: IdentityProviderIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> IdentityProviderOut:
    idp = IdentityProvider(
        name=payload.name,
        provider_type=payload.provider_type,
        enabled=payload.enabled,
        auto_provision_users=payload.auto_provision_users,
        sync_groups=payload.sync_groups,
        order=payload.order,
    )
    for field in _IDP_PLAIN_FIELDS:
        setattr(idp, field, getattr(payload, field))
    idp.ldap_bind_password_enc = (
        encrypt(payload.ldap_bind_password) if payload.ldap_bind_password else None
    )
    idp.entra_client_secret_enc = (
        encrypt(payload.entra_client_secret) if payload.entra_client_secret else None
    )
    db.add(idp)
    db.commit()
    db.refresh(idp)
    return _idp_out(idp)


@router.patch("/identity-providers/{idp_id}", response_model=IdentityProviderOut)
def update_identity_provider(
    idp_id: int,
    payload: IdentityProviderUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> IdentityProviderOut:
    idp = db.get(IdentityProvider, idp_id)
    if idp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Identity provider not found")

    if payload.name is not None:
        idp.name = payload.name
    if payload.provider_type is not None:
        idp.provider_type = payload.provider_type
    if payload.enabled is not None:
        idp.enabled = payload.enabled
    if payload.auto_provision_users is not None:
        idp.auto_provision_users = payload.auto_provision_users
    if payload.sync_groups is not None:
        idp.sync_groups = payload.sync_groups
    if payload.order is not None:
        idp.order = payload.order

    for field in _IDP_PLAIN_FIELDS:
        value = getattr(payload, field)
        if value is not None:
            setattr(idp, field, value)

    idp.ldap_bind_password_enc = _apply_secret(
        idp.ldap_bind_password_enc, payload.ldap_bind_password
    )
    idp.entra_client_secret_enc = _apply_secret(
        idp.entra_client_secret_enc, payload.entra_client_secret
    )

    db.commit()
    db.refresh(idp)
    return _idp_out(idp)


@router.delete("/identity-providers/{idp_id}", response_model=Message)
def delete_identity_provider(
    idp_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> Message:
    idp = db.get(IdentityProvider, idp_id)
    if idp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Identity provider not found")
    db.delete(idp)
    db.commit()
    return Message(detail="Identity provider deleted")


def _test_ldap(idp: IdentityProvider) -> ConnectionTestResult:
    if not idp.ldap_host:
        return ConnectionTestResult(ok=False, message="LDAP host is not configured")
    try:
        import ldap3  # type: ignore
    except Exception as exc:  # noqa: BLE001 - dependency may not be installed
        return ConnectionTestResult(ok=False, message=f"ldap3 not available: {exc}")

    try:
        port = idp.ldap_port or (636 if idp.ldap_use_ssl else 389)
        server = ldap3.Server(
            idp.ldap_host,
            port=port,
            use_ssl=idp.ldap_use_ssl,
            connect_timeout=10,
        )
        bind_password = decrypt(idp.ldap_bind_password_enc) if idp.ldap_bind_password_enc else None
        conn_kwargs = {"auto_bind": True, "receive_timeout": 10}
        if idp.ldap_bind_dn and bind_password:
            conn_kwargs["user"] = idp.ldap_bind_dn
            conn_kwargs["password"] = bind_password
        conn = ldap3.Connection(server, **conn_kwargs)
        bound = bool(conn.bound)
        conn.unbind()
        if bound:
            return ConnectionTestResult(ok=True, message="LDAP bind successful")
        return ConnectionTestResult(ok=False, message="LDAP connection failed")
    except Exception as exc:  # noqa: BLE001
        return ConnectionTestResult(ok=False, message=str(exc) or "LDAP connection failed")


def _test_entra(idp: IdentityProvider) -> ConnectionTestResult:
    if not (idp.entra_tenant_id and idp.entra_client_id):
        return ConnectionTestResult(
            ok=False, message="Entra tenant_id and client_id are required"
        )
    if not idp.entra_client_secret_enc:
        return ConnectionTestResult(ok=False, message="Entra client secret is not configured")
    try:
        import httpx  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return ConnectionTestResult(ok=False, message=f"httpx not available: {exc}")

    url = (
        f"https://login.microsoftonline.com/{idp.entra_tenant_id}"
        "/v2.0/.well-known/openid-configuration"
    )
    try:
        resp = httpx.get(url, timeout=10.0)
        if resp.status_code == 200:
            return ConnectionTestResult(
                ok=True, message="Entra OIDC discovery document reachable"
            )
        return ConnectionTestResult(
            ok=False, message=f"OIDC discovery returned HTTP {resp.status_code}"
        )
    except Exception as exc:  # noqa: BLE001
        return ConnectionTestResult(ok=False, message=str(exc) or "Entra discovery failed")


@router.post("/identity-providers/{idp_id}/test", response_model=ConnectionTestResult)
def test_identity_provider(
    idp_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> ConnectionTestResult:
    idp = db.get(IdentityProvider, idp_id)
    if idp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Identity provider not found")
    if idp.provider_type == "ldap":
        return _test_ldap(idp)
    if idp.provider_type == "entra":
        return _test_entra(idp)
    return ConnectionTestResult(
        ok=False, message=f"Unsupported provider type: {idp.provider_type}"
    )


# ===========================================================================
# GLOBAL PERMISSIONS
# ===========================================================================
@router.get("/global-permissions", response_model=list[GlobalGrantOut])
def list_global_permissions(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> list[GlobalGrantOut]:
    grants = db.scalars(
        select(GlobalPermissionGrant).order_by(GlobalPermissionGrant.id)
    ).all()
    return [GlobalGrantOut.model_validate(g) for g in grants]


@router.post("/global-permissions", response_model=GlobalGrantOut, status_code=status.HTTP_201_CREATED)
def create_global_permission(
    payload: GlobalGrantIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> GlobalGrantOut:
    if payload.permission not in P.GLOBAL_PERMISSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown global permission: {payload.permission}",
        )
    if payload.holder_type not in (P.HOLDER_GROUP, P.HOLDER_USER):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="holder_type must be 'group' or 'user'",
        )

    existing = db.scalars(
        select(GlobalPermissionGrant).where(
            GlobalPermissionGrant.permission == payload.permission,
            GlobalPermissionGrant.holder_type == payload.holder_type,
            GlobalPermissionGrant.holder_value == payload.holder_value,
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Grant already exists",
        )

    grant = GlobalPermissionGrant(
        permission=payload.permission,
        holder_type=payload.holder_type,
        holder_value=payload.holder_value,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return GlobalGrantOut.model_validate(grant)


@router.delete("/global-permissions/{grant_id}", response_model=Message)
def delete_global_permission(
    grant_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_site_admin),
) -> Message:
    grant = db.get(GlobalPermissionGrant, grant_id)
    if grant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found")
    db.delete(grant)
    db.commit()
    return Message(detail="Grant deleted")
