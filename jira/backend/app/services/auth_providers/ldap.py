"""LDAP / Active Directory authentication via bind-and-search.

Flow:
1. Bind with the service account (or anonymously) and SEARCH for the user using
   ``ldap_user_filter`` under ``ldap_user_base_dn``.
2. Re-bind as the discovered user DN with the supplied password to verify the
   credentials.
3. Read the configured username/email/display-name attributes and resolve the
   user's groups via ``ldap_group_filter`` under ``ldap_group_base_dn``.

``ldap3`` is imported lazily so this module imports cleanly without it. No
function raises — failures yield ``None`` (authenticate) or ``(False, msg)``
(test_connection).
"""
from __future__ import annotations

import logging

from app.core.crypto import decrypt
from app.models.identity import IdentityProvider

logger = logging.getLogger(__name__)


def _server(provider: IdentityProvider):
    import ldap3

    return ldap3.Server(
        host=provider.ldap_host,
        port=provider.ldap_port or (636 if provider.ldap_use_ssl else 389),
        use_ssl=bool(provider.ldap_use_ssl),
        get_info=ldap3.NONE,
    )


def _first_value(entry, attr: str):
    """Return a single attribute value from an ldap3 entry (or None)."""
    try:
        values = entry["attributes"].get(attr)
    except (KeyError, TypeError):
        return None
    if isinstance(values, (list, tuple)):
        return values[0] if values else None
    return values


def _service_bind(provider: IdentityProvider):
    """Open a connection bound as the service account (or anonymously)."""
    import ldap3

    server = _server(provider)
    bind_dn = provider.ldap_bind_dn or None
    password = decrypt(provider.ldap_bind_password_enc) if bind_dn else None
    conn = ldap3.Connection(
        server,
        user=bind_dn,
        password=password,
        auto_bind=True,
        authentication=ldap3.SIMPLE if bind_dn else ldap3.ANONYMOUS,
        read_only=True,
    )
    return conn


def _resolve_groups(conn, provider: IdentityProvider, user_dn: str, username: str) -> list[str]:
    """Resolve the user's group names via the configured group filter."""
    if not provider.ldap_group_filter or not provider.ldap_group_base_dn:
        return []
    try:
        group_filter = provider.ldap_group_filter.format(user_dn=user_dn, username=username)
    except (KeyError, IndexError):
        group_filter = provider.ldap_group_filter
    attr = provider.ldap_attr_group_name or "cn"
    try:
        conn.search(
            search_base=provider.ldap_group_base_dn,
            search_filter=group_filter,
            attributes=[attr],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LDAP group search failed for %s: %s", user_dn, exc)
        return []
    groups: list[str] = []
    for entry in conn.response or []:
        name = _first_value(entry, attr)
        if name:
            groups.append(str(name))
    return groups


def authenticate(provider: IdentityProvider, username: str, password: str) -> dict | None:
    """Authenticate *username*/*password* against the LDAP *provider*.

    Returns ``{"username", "email", "display_name", "dn", "groups": [...]}`` on
    success, or ``None`` on any failure. Never raises.
    """
    if not username or not password or not provider.ldap_host:
        return None

    import ldap3

    conn = None
    try:
        conn = _service_bind(provider)

        user_filter_tmpl = provider.ldap_user_filter or "(uid={username})"
        try:
            user_filter = user_filter_tmpl.format(username=username)
        except (KeyError, IndexError):
            user_filter = user_filter_tmpl

        attr_username = provider.ldap_attr_username or "uid"
        attr_email = provider.ldap_attr_email or "mail"
        attr_display = provider.ldap_attr_display_name or "cn"

        conn.search(
            search_base=provider.ldap_user_base_dn,
            search_filter=user_filter,
            attributes=[attr_username, attr_email, attr_display],
        )
        if not conn.response:
            return None
        # Take the first concrete searchResEntry.
        entry = None
        for item in conn.response:
            if item.get("type") == "searchResEntry" and item.get("dn"):
                entry = item
                break
        if entry is None:
            return None
        user_dn = entry["dn"]

        # Verify the password by binding as the user DN.
        verify = ldap3.Connection(
            _server(provider),
            user=user_dn,
            password=password,
            authentication=ldap3.SIMPLE,
            read_only=True,
        )
        if not verify.bind():
            verify.unbind()
            return None
        verify.unbind()

        groups = _resolve_groups(conn, provider, user_dn, username)

        return {
            "username": str(_first_value(entry, attr_username) or username),
            "email": _first_value(entry, attr_email),
            "display_name": str(
                _first_value(entry, attr_display) or _first_value(entry, attr_username) or username
            ),
            "dn": user_dn,
            "groups": groups,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("LDAP authentication error for %s: %s", username, exc)
        return None
    finally:
        if conn is not None:
            try:
                conn.unbind()
            except Exception:  # noqa: BLE001
                pass


def test_connection(provider: IdentityProvider) -> tuple[bool, str]:
    """Bind (service account or anonymous) and report connectivity status."""
    if not provider.ldap_host:
        return (False, "LDAP host not configured")
    try:
        import ldap3  # noqa: F401
    except ImportError:
        return (False, "ldap3 package is not installed")
    conn = None
    try:
        conn = _service_bind(provider)
        return (True, "Bind succeeded")
    except Exception as exc:  # noqa: BLE001
        return (False, f"Bind failed: {exc}")
    finally:
        if conn is not None:
            try:
                conn.unbind()
            except Exception:  # noqa: BLE001
                pass
