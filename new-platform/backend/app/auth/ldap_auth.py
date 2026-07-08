"""LDAP bind authentication + group→role mapping. Only imported when AUTH_MODE=ldap."""
from __future__ import annotations

import json

from ..config import get_settings
from .rbac import User


def authenticate(username: str, password: str) -> User:
    import ldap3  # optional dep (requirements-live.txt)

    s = get_settings()
    if not password:
        raise ValueError("Empty password")
    bind_dn = s.ldap_bind_dn_template.format(username=username)
    server = ldap3.Server(s.ldap_url, get_info=ldap3.NONE, connect_timeout=10)
    conn = ldap3.Connection(server, user=bind_dn, password=password, auto_bind=True)
    try:
        display, email, groups = username, "", []
        if s.ldap_user_search_base:
            conn.search(
                s.ldap_user_search_base,
                s.ldap_user_filter.format(username=ldap3.utils.conv.escape_filter_chars(username)),
                attributes=["displayName", "mail", "memberOf", "sAMAccountName"],
            )
            if conn.entries:
                e = conn.entries[0]
                display = str(e.displayName) if e.displayName else username
                email = str(e.mail) if e.mail else ""
                groups = [str(g) for g in (e.memberOf.values if e.memberOf else [])]
        role_map = json.loads(s.ldap_group_role_map or "{}")
        raw_roles, teams = [], []
        for g in groups:
            cn = g.split(",", 1)[0].removeprefix("CN=")
            if g in role_map:
                raw_roles.append(role_map[g])
            elif cn in role_map:
                raw_roles.append(role_map[cn])
            else:
                teams.append(cn)
        return User(username=username, display_name=display, email=email,
                    raw_roles=raw_roles or ["developer"], teams=teams)
    finally:
        conn.unbind()
