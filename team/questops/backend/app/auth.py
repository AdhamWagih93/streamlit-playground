"""Login: LDAP group-gated in live mode, seeded users in demo mode.
Sessions are short-lived HS256 JWTs."""

import datetime as dt

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .db import User, get_db, utcnow

DEMO_USERS = {
    "alice": {"display_name": "Alice Nasr", "email": "alice@demo.local"},
    "bob": {"display_name": "Bob Farid", "email": "bob@demo.local"},
    "carol": {"display_name": "Carol Adel", "email": "carol@demo.local"},
    "dave": {"display_name": "Dave Samir", "email": "dave@demo.local"},
}


def role_for(username: str) -> str:
    """One group, per-username roles: approver by default, plain member
    only when the username is listed in MEMBER_USERNAMES."""
    return "member" if username.lower() in settings.member_users else "approver"


def _ldap_authenticate(username: str, password: str) -> dict | None:
    import ldap3

    server = ldap3.Server(settings.ldap_url, get_info=ldap3.NONE)
    svc = ldap3.Connection(server, user=settings.ldap_bind_dn,
                           password=settings.ldap_bind_password, auto_bind=True)
    try:
        svc.search(settings.ldap_base_dn,
                   f"({settings.ldap_user_attr}={ldap3.utils.conv.escape_filter_chars(username)})",
                   attributes=["memberOf", "displayName", "mail", "cn"])
        if not svc.entries:
            return None
        entry = svc.entries[0]
        user_dn = entry.entry_dn
        groups = {str(g).lower() for g in (entry.memberOf.values if "memberOf" in entry else [])}
    finally:
        svc.unbind()

    if settings.ldap_required_group and settings.ldap_required_group.lower() not in groups:
        return None  # authenticated identity but not in the team group

    # verify the password by binding as the user
    try:
        ldap3.Connection(server, user=user_dn, password=password, auto_bind=True).unbind()
    except ldap3.core.exceptions.LDAPException:
        return None

    display = str(entry.displayName) if "displayName" in entry else username
    mail = str(entry.mail) if "mail" in entry else ""
    return {"username": username, "display_name": display, "email": mail,
            "role": role_for(username)}


def list_group_members() -> list[dict]:
    """Everyone in the team group — the roster shown even before first login."""
    if settings.demo_mode:
        return [{"username": u, **m} for u, m in DEMO_USERS.items()]
    if not (settings.ldap_url and settings.ldap_required_group):
        return []
    import ldap3

    server = ldap3.Server(settings.ldap_url, get_info=ldap3.NONE)
    conn = ldap3.Connection(server, user=settings.ldap_bind_dn,
                            password=settings.ldap_bind_password, auto_bind=True)
    try:
        conn.search(settings.ldap_base_dn,
                    f"(memberOf={ldap3.utils.conv.escape_filter_chars(settings.ldap_required_group)})",
                    attributes=[settings.ldap_user_attr, "displayName", "mail"])
        out = []
        for e in conn.entries:
            uname = (str(getattr(e, settings.ldap_user_attr))
                     if settings.ldap_user_attr in e else "")
            if not uname:
                continue
            out.append({"username": uname.lower(),
                        "display_name": str(e.displayName) if "displayName" in e else uname,
                        "email": str(e.mail) if "mail" in e else ""})
        return out
    finally:
        conn.unbind()


_LDAP_GROUP_CACHE: dict = {}  # cn -> {"at": ts, "members": [...]}
_LDAP_GROUP_TTL = 3600

# demo LDAP groups referenced by the demo ADO project descriptions ([TEAM])
_DEMO_LDAP_GROUPS = {
    "platform-devs": ["Alice Nasr", "Bob Farid", "Carol Adel", "Dave Samir"],
    "control-owners": ["Alice Nasr", "Bob Farid"],
    "research-team": ["Carol Adel"],
}


def ldap_group_members(cn: str) -> dict:
    """Resolve an LDAP group by CN across all configured servers. Returns
    {"found": bool, "members": [{username, display_name}]}. 'found' is True
    even when the group has zero members — distinct from 'group not found on
    any server', so a real-but-empty group is NOT mistaken for missing.
    Cached 1h; LDAP outages return the stale/empty result rather than raising."""
    import time
    cn = (cn or "").strip()
    if not cn:
        return {"found": False, "members": []}
    hit = _LDAP_GROUP_CACHE.get(cn.lower())
    if hit and time.time() - hit["at"] < _LDAP_GROUP_TTL:
        return hit["value"]
    if settings.demo_mode:
        raw = _DEMO_LDAP_GROUPS.get(cn.lower())
        value = {"found": raw is not None,
                 "members": [{"username": m.split()[0].lower(), "display_name": m}
                             for m in (raw or [])]}
        _LDAP_GROUP_CACHE[cn.lower()] = {"at": time.time(), "value": value}
        return value
    # try each configured LDAP server; the group may live in any directory
    for srv in settings.ldap_servers:
        if not (srv["url"] and srv["bind_dn"]):
            continue
        res = _ldap_group_on_server(srv, cn)
        if res is not None:  # found on this server (even if empty)
            _LDAP_GROUP_CACHE[cn.lower()] = {"at": time.time(), "value": res}
            return res
    return hit["value"] if hit else {"found": False, "members": []}


# group object classes across AD / OpenLDAP / RFC2307
_LDAP_GROUP_CLASSES = ("group", "groupOfNames", "groupOfUniqueNames",
                       "groupOfMembers", "posixGroup")


def _rdn_value(dn: str) -> tuple[str, str]:
    """(attr, value) of a DN's leading RDN, e.g. 'CN=John Doe,...' -> ('cn','John Doe')."""
    rdn = str(dn).split(",")[0]
    k, _, v = rdn.partition("=")
    return k.strip().lower(), v.strip()


def _ldap_group_on_server(srv: dict, cn: str) -> dict | None:
    """{"found": True, "members": [...]} if group `cn` exists on this server,
    else None (caller tries the next). Members resolved robustly: the memberOf
    back-link (AD), then the group's own member / uniqueMember / memberUid
    attributes (OpenLDAP / posix), since not every directory maintains memberOf."""
    try:
        import ldap3
        esc = ldap3.utils.conv.escape_filter_chars
        attr = srv["user_attr"]
        server = ldap3.Server(srv["url"], get_info=ldap3.NONE)
        conn = ldap3.Connection(server, user=srv["bind_dn"],
                                password=srv["bind_password"], auto_bind=True)
        try:
            oc = "".join(f"(objectClass={c})" for c in _LDAP_GROUP_CLASSES)
            conn.search(srv["base_dn"], f"(&(cn={esc(cn)})(|{oc}))",
                        attributes=["member", "uniqueMember", "memberUid"])
            if not conn.entries:
                return None  # not on this server — try the next
            grp = conn.entries[0]
            gdn = grp.entry_dn
            members: dict[str, dict] = {}

            def add(username: str, display: str):
                key = (username or display).lower()
                if key and key not in members:
                    members[key] = {"username": (username or "").lower(),
                                    "display_name": display or username}

            # 1) back-link: users whose memberOf points at the group (AD)
            conn.search(srv["base_dn"], f"(memberOf={esc(gdn)})",
                        attributes=[attr, "displayName"])
            for e in conn.entries:
                uname = str(getattr(e, attr)) if attr in e else ""
                if uname:
                    add(uname, str(e.displayName) if "displayName" in e else uname)
            # 2) posixGroup: memberUid holds usernames directly
            if "memberUid" in grp:
                for uid in grp.memberUid.values:
                    add(str(uid), str(uid))
            # 3) groupOfNames / groupOfUniqueNames: member/uniqueMember are DNs
            dns = []
            for a in ("member", "uniqueMember"):
                if a in grp:
                    dns += list(getattr(grp, a).values)
            for mdn in dns[:5000]:
                k, v = _rdn_value(mdn)
                if v:
                    add(v if k in ("uid", "samaccountname") else "", v)
            return {"found": True, "members": list(members.values())}
        finally:
            conn.unbind()
    except Exception:  # noqa: BLE001 — this server unreachable; try the next
        return None


_ROSTER_CACHE: dict = {"at": 0.0, "rows": []}
_ROSTER_TTL = 600  # seconds


def sync_group_members(db: Session) -> None:
    """Upsert the whole group into the users table so the leaderboard always
    lists everyone, XP or not. Cached; LDAP hiccups never break callers."""
    import time

    try:
        if time.time() - _ROSTER_CACHE["at"] > _ROSTER_TTL:
            _ROSTER_CACHE["rows"] = list_group_members()
            _ROSTER_CACHE["at"] = time.time()
    except Exception:  # noqa: BLE001 — stale roster beats a dead leaderboard
        return
    for m in _ROSTER_CACHE["rows"]:
        user = db.get(User, m["username"])
        if user is None:
            user = User(username=m["username"])
            db.add(user)
        user.display_name = m["display_name"] or user.display_name
        user.email = m["email"] or user.email
        user.role = role_for(m["username"])
    db.commit()


def authenticate(username: str, password: str) -> dict | None:
    username = username.strip().lower()
    if settings.demo_mode:
        profile = DEMO_USERS.get(username)
        if profile and password == settings.demo_password:
            return {"username": username, "role": role_for(username), **profile}
        return None
    # live mode: demo accounts must never work
    if not settings.ldap_url:
        raise RuntimeError("demo mode is off but LDAP_URL is not configured — no way to log in")
    if not password:
        return None
    return _ldap_authenticate(username, password)


def make_token(profile: dict) -> str:
    payload = {
        "sub": profile["username"],
        "name": profile["display_name"],
        "role": profile["role"],
        "exp": utcnow() + dt.timedelta(hours=settings.token_ttl_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def upsert_user(db: Session, profile: dict) -> User:
    user = db.get(User, profile["username"])
    if user is None:
        user = User(username=profile["username"])
        db.add(user)
    user.display_name = profile["display_name"]
    user.email = profile.get("email", "")
    user.role = profile["role"]
    db.commit()
    return user


def current_user(authorization: str = Header(default=""),
                 db: Session = Depends(get_db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing token")
    try:
        payload = jwt.decode(authorization[7:], settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token")
    user = db.get(User, payload["sub"])
    if user is None:
        raise HTTPException(401, "unknown user")
    return user


def require_approver(user: User = Depends(current_user)) -> User:
    if user.role != "approver":
        raise HTTPException(403, "approver role required")
    return user
