"""Login: LDAP group-gated in live mode, seeded users in demo mode.
Sessions are short-lived HS256 JWTs."""

import datetime as dt
import os
import re
import shutil
import subprocess
import tempfile

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


_LDAP_GROUP_CACHE: dict = {}  # cn -> {"at": ts, "value": {...}}
_LDAP_GROUP_TTL = 3600

# demo LDAP groups referenced by the demo ADO project descriptions ([TEAM])
_DEMO_LDAP_GROUPS = {
    "platform-devs": ["Alice Nasr", "Bob Farid", "Carol Adel", "Dave Samir"],
    "control-owners": ["Alice Nasr", "Bob Farid"],
    "research-team": ["Carol Adel"],
}

# [TEAM] members are resolved by running an asset the user's cloned Engine repo
# ships: scripts/Tools/LDAP/getTeamMembersCN.sh <team> prints that team's members.
# The script (a) sources a .prd profile via `. $HOME/.prd` — where .prd is the
# file at the Engine repo ROOT, expected under the runner's REAL $HOME — and
# (b) does work relative to the current directory, so it must run from INSIDE
# the Engine repo. So we copy <engine>/.prd to $HOME/.prd and run with cwd set
# to the repo (leaving $HOME untouched).
_TEAM_SCRIPT_REL = "scripts/Tools/LDAP/getTeamMembersCN.sh"
_TEAM_SCRIPT_TIMEOUT = 60


def _engine_dir():
    """The cloned Engine repo's server copy (a Path), or None when the repo is
    not defined on the Repositories page or has not been cloned yet."""
    from .integrations import repos
    try:
        engine = next((r for r in repos.configured()
                       if (r.get("name") or "").lower() == "engine"), None)
        if not engine:
            return None
        d = repos._dir_for(engine)
        return d if d.exists() else None
    except Exception:  # noqa: BLE001 — resolution never breaks the caller
        return None


def team_source_status() -> dict:
    """Health of the [TEAM]-resolution mechanism (the Engine repo's
    getTeamMembersCN.sh + the .prd profile it sources) for the Access page."""
    row = {"mechanism": "engine-script", "script": _TEAM_SCRIPT_REL,
           "engine_cloned": False, "script_present": False, "prd_present": False,
           "healthy": False, "note": ""}
    if settings.demo_mode:
        return {**row, "engine_cloned": True, "script_present": True,
                "prd_present": True, "healthy": True, "note": "demo groups"}
    d = _engine_dir()
    if d is None:
        row["note"] = "Engine repo not defined / not cloned (Repositories page)"
        return row
    row["engine_cloned"] = True
    row["script_present"] = (d / _TEAM_SCRIPT_REL).exists()
    row["prd_present"] = (d / ".prd").exists()
    if not row["script_present"]:
        row["note"] = f"{_TEAM_SCRIPT_REL} missing in the Engine repo"
    elif not row["prd_present"]:
        row["note"] = ".prd profile missing at the Engine repo root (copied to $HOME/.prd at run time)"
    else:
        row["healthy"], row["note"] = True, "script + .prd present"
    return row


# LDIF attribute prefixes a raw ldapsearch dump emits before the useful value
_LDIF_ATTR = re.compile(r"^(?:dn|member|uniquemember|memberuid|cn|uid|"
                        r"samaccountname|displayname|name)\s*:\s*(.+)$", re.I)


def _parse_team_members(out: str) -> list[dict]:
    """getTeamMembersCN.sh prints one member per line. Tolerant of the common
    shapes such a script emits so an output-format quirk doesn't silently yield
    zero members:
      - a bare username or display name           -> jdoe / John Doe
      - 'username<delim>Display Name'              -> jdoe,John Doe (,/tab/|/;)
      - a raw LDIF line                            -> member: CN=John Doe,OU=...
      - a full or partial DN                       -> CN=John Doe,OU=... / uid=jdoe
    Both username and display_name are set, so matching against ADO grantees
    works whether ADO surfaces the login or the display name."""
    members: list[dict] = []
    seen: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LDIF_ATTR.match(line)              # strip an "attr: " LDIF prefix
        if m:
            line = m.group(1).strip()
        # a DN (CN=John Doe,OU=...) or single RDN (uid=jdoe): take the RDN value
        if re.match(r"^[A-Za-z][\w-]*=", line):
            head = line.split(",", 1)[0]
            if "=" in head:
                line = head.split("=", 1)[1].strip()
        parts = [p.strip() for p in re.split(r"[,\t|;]", line) if p.strip()]
        if not parts:
            continue
        uname, disp = parts[0], (parts[1] if len(parts) > 1 else parts[0])
        key = uname.lower()
        if key in seen:
            continue
        seen.add(key)
        members.append({"username": uname.lower(), "display_name": disp})
    return members


def _run_team_script(cn: str) -> dict:
    """Low-level: run getTeamMembersCN.sh <cn> once and return the RAW result
    {ok, returncode, stdout, stderr, error}. 'ok' means the script actually ran
    (regardless of exit code); 'error' is set only when it could not run at all
    (Engine/script missing, timeout, spawn failure). Shared by the resolver and
    the on-page health probe so both execute the script identically."""
    import time
    d = _engine_dir()
    if d is None:
        return {"ok": False, "error": "Engine repo not defined / not cloned"}
    script = d / _TEAM_SCRIPT_REL
    if not script.exists():
        return {"ok": False, "error": f"{_TEAM_SCRIPT_REL} missing in the Engine repo"}
    # the script does `. $HOME/.prd`, expecting the Engine repo's root .prd under
    # the runner's real $HOME — place it there (atomically: team resolution runs
    # in parallel threads, so a half-written .prd must never be sourced).
    home = os.environ.get("HOME") or os.path.expanduser("~")
    prd_src = d / ".prd"
    if prd_src.exists():
        err = _install_prd(prd_src, home)
        if err:
            return {"ok": False, "error": err}
    t0 = time.time()
    try:
        # run from INSIDE the Engine repo (cwd), with the real $HOME intact so
        # `. $HOME/.prd` resolves to the copy we just placed
        p = subprocess.run(["bash", str(script), cn], cwd=str(d),
                           capture_output=True, text=True,
                           timeout=_TEAM_SCRIPT_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": f"script error: {str(exc)[:120]}"}
    return {"ok": True, "returncode": p.returncode, "stdout": p.stdout,
            "stderr": p.stderr, "duration_ms": int((time.time() - t0) * 1000)}


def _install_prd(prd_src, home: str) -> str | None:
    """Copy the Engine repo's .prd to $HOME/.prd atomically (temp + rename on
    the same filesystem). Returns an error string on failure, else None."""
    dest = os.path.join(home, ".prd")
    try:
        os.makedirs(home, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=home, prefix=".prd.", suffix=".tmp")
        os.close(fd)
        shutil.copyfile(prd_src, tmp)
        os.replace(tmp, dest)  # atomic — concurrent runners never see a partial file
        return None
    except OSError as exc:
        return f"could not place .prd under $HOME ({home}): {str(exc)[:100]}"


def _resolve_team_via_script(cn: str) -> dict:
    """Run the Engine repo's getTeamMembersCN.sh for team `cn`. 'found' is True on
    a clean (exit 0) run — even for an empty team — and False when the team
    can't be resolved (Engine/script absent or a non-zero exit)."""
    r = _run_team_script(cn)
    if not r["ok"]:
        return {"found": False, "members": [], "note": r["error"]}
    if r["returncode"] != 0:
        tail = (r["stderr"] or r["stdout"] or "").strip().splitlines()
        return {"found": False, "members": [],
                "note": f"exit {r['returncode']}: {(tail[-1] if tail else '')[:100]}"}
    return {"found": True, "members": _parse_team_members(r["stdout"])}


_PROBE_CAP = 6000  # cap raw stdout/stderr echoed to the page


def probe_team_resolver(cn: str) -> dict:
    """On-page health probe: run getTeamMembersCN.sh <team> and return the RAW
    stdout/stderr/exit code alongside what QuestOps PARSED, so a mismatch
    between the script's output format and the parser is visible at a glance."""
    cn = (cn or "").strip()
    out = {"team": cn, "ran": False, "returncode": None, "duration_ms": None,
           "stdout": "", "stderr": "", "members": [], "parsed_count": 0,
           "note": "", "demo": bool(settings.demo_mode)}
    if not cn:
        out["note"] = "enter a team name to test"
        return out
    if "\n" in cn or "\x00" in cn:
        out["note"] = "invalid team name"
        return out
    if settings.demo_mode:
        v = ldap_group_members(cn)
        out.update(ran=True, returncode=0, members=v["members"],
                   parsed_count=len(v["members"]),
                   stdout="\n".join(m["display_name"] for m in v["members"]),
                   note="demo mode — seeded groups, the script is not executed")
        return out
    r = _run_team_script(cn)
    if not r["ok"]:
        out["note"] = r["error"]
        return out
    members = _parse_team_members(r["stdout"])
    out.update(ran=True, returncode=r["returncode"], duration_ms=r["duration_ms"],
               stdout=r["stdout"][:_PROBE_CAP], stderr=r["stderr"][:_PROBE_CAP],
               members=members, parsed_count=len(members),
               note=("ok" if r["returncode"] == 0 else f"non-zero exit {r['returncode']}"))
    if r["returncode"] == 0 and not members:
        out["note"] = ("script ran but QuestOps parsed 0 members — check the raw "
                       "output below; members must be one per line")
    return out


def ldap_group_members(cn: str) -> dict:
    """Resolve a project's [TEAM] group to its members. Returns
    {"found": bool, "members": [{username, display_name}]}. In live mode this
    runs the cloned Engine repo's scripts/Tools/LDAP/getTeamMembersCN.sh <team>;
    'found' is True on a clean run (even for an empty team — distinct from an
    unresolvable one), which the caller uses to drive ldap_resolved. Cached 1h;
    a resolution failure keeps any previous good result rather than raising."""
    import time
    cn = (cn or "").strip()
    if not cn or "\n" in cn or "\x00" in cn:
        return {"found": False, "members": []}
    if cn.lower() == "unassigned":     # not a real group; the caller special-cases it
        return {"found": True, "members": []}
    key = cn.lower()
    hit = _LDAP_GROUP_CACHE.get(key)
    if hit and time.time() - hit["at"] < _LDAP_GROUP_TTL:
        return hit["value"]
    if settings.demo_mode:
        raw = _DEMO_LDAP_GROUPS.get(key)
        value = {"found": raw is not None,
                 "members": [{"username": m.split()[0].lower(), "display_name": m}
                             for m in (raw or [])]}
    else:
        value = _resolve_team_via_script(cn)
        if not value.get("found") and hit:  # keep the stale-but-good result
            return hit["value"]
    _LDAP_GROUP_CACHE[key] = {"at": time.time(), "value": value}
    return value


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
