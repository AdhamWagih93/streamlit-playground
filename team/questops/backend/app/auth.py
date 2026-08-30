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
    only when the username is listed in MEMBER_USERNAMES. RESTRICTED_USERS
    are always plain members — they exist to view their allowed pages."""
    u = username.lower()
    if u in settings.restricted_user_set:
        return "member"
    return "member" if u in settings.member_users else "approver"


_USER_EXISTS: dict = {}


def _identity_keys(name: str) -> set[str]:
    """Spellings under which one identity may appear: login, display name with
    '.', '_' or space separators, @domain stripped — all lower-case."""
    n = (name or "").strip().lower()
    if "\\" in n:                       # DOMAIN\user → user
        n = n.rsplit("\\", 1)[-1]
    if "@" in n:
        n = n.split("@", 1)[0]
    n = n.strip()
    if not n:
        return set()
    return {n, n.replace("_", " "), n.replace(".", " "), n.replace("_", "."),
            n.replace(" ", "_"), n.replace(".", "_"), n.replace(" ", ".")}


_KNOWN_IDENTITIES: dict = {"at": 0.0, "keys": None}
_KNOWN_TTL = 3600


def known_identities() -> set[str] | None:
    """Union of every member the team script returns for every team named in
    the inventory (dev/qc/ops/prd teams of every project) plus the login group.
    This is THE directory QuestOps knows — no direct LDAP query is made for
    identity checks. None when nothing could be resolved (Engine not cloned)."""
    import time
    if _KNOWN_IDENTITIES["keys"] is not None and time.time() - _KNOWN_IDENTITIES["at"] < _KNOWN_TTL:
        return _KNOWN_IDENTITIES["keys"]
    teams: list[str] = []
    try:
        from .integrations import inventory
        for p in inventory.parse().get("projects") or []:
            for t in (p.get("teams") or {}).values():
                if t and t not in teams:
                    teams.append(t)
    except Exception:  # noqa: BLE001 — inventory may be absent
        pass
    grp = _group_cn(settings.ldap_required_group)
    if grp and grp not in teams:
        teams.append(grp)
    keys: set[str] = set()
    resolved = 0
    for t in teams:
        for cand in dict.fromkeys((t, t.replace("_", "-"), t.replace(" ", "-"), t.replace(" ", "_"), t.replace("-", "_"))):
            res = ldap_group_members(cand)
            if res.get("found"):
                resolved += 1
                for m in res.get("members") or []:
                    keys |= _identity_keys(m.get("username") or "")
                    keys |= _identity_keys(m.get("display_name") or "")
                    disp = (m.get("display_name") or "").strip().lower()
                    if disp:
                        keys.add(disp.split()[0])
                break
    value = keys if resolved else None
    _KNOWN_IDENTITIES.update(at=time.time(), keys=value)
    return value


def ldap_user_exists(name: str) -> bool | None:
    """Is this identity known to the directory? True/False, or None when it
    can't be told (Engine script unavailable). Answered ONLY from the team
    script's output across every inventory team (see known_identities) —
    never by binding to LDAP. Cached for an hour per identity."""
    import time
    mine = _identity_keys(name)          # DOMAIN\user / user@domain → user spellings
    if not mine:
        return None
    key = min(mine)
    hit = _USER_EXISTS.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    if settings.demo_mode:
        known = {u.lower() for u in DEMO_USERS}
        for members in _DEMO_LDAP_GROUPS.values():
            for m in members:
                known.add(m.lower()); known.add(m.split()[0].lower())
                known.add(m.lower().replace(" ", "_")); known.add(m.lower().replace(" ", "."))
        res = bool(mine & known)
    else:
        ks = known_identities()
        res = None if ks is None else bool(mine & ks)
    _USER_EXISTS[key] = (time.time(), res)
    return res


def pages_for(username: str) -> tuple[bool, list[str] | None, list[str]]:
    """(restricted?, allowed pages or None=all, pages hidden from this user).
    Restricted users see ONLY restricted_pages; FULL_ACCESS_USERS see every
    page including the restricted ones (winning over a restricted listing);
    everyone else sees everything EXCEPT restricted_pages."""
    u = username.lower()
    if u in settings.full_access_user_set:
        return False, None, []
    if u in settings.restricted_user_set:
        return True, settings.restricted_page_list, []
    return False, None, settings.restricted_page_list


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

    if (settings.ldap_required_group
            and settings.ldap_required_group.lower() not in groups
            and username.strip().lower() not in settings.restricted_user_set):
        return None  # not in the team group and not individually allowed

    # verify the password by binding as the user — unless the instance runs
    # with LOGIN_WITHOUT_PASSWORD (identity + group gates above still apply)
    if not settings.login_without_password:
        try:
            ldap3.Connection(server, user=user_dn, password=password, auto_bind=True).unbind()
        except ldap3.core.exceptions.LDAPException:
            return None

    display = str(entry.displayName) if "displayName" in entry else username
    mail = str(entry.mail) if "mail" in entry else ""
    return {"username": username, "display_name": display, "email": mail,
            "role": role_for(username)}


def _group_cn(group: str) -> str:
    """'CN=DevOps-Team,OU=Groups,DC=corp' → 'DevOps-Team'; a bare name passes through."""
    g = (group or "").strip()
    if "=" in g:
        head = g.split(",", 1)[0]
        g = head.split("=", 1)[1].strip() if "=" in head else g
    return g


def list_group_members() -> list[dict]:
    """Everyone in the login team group — the roster shown even before first
    login. Resolved through the Engine team script (never a direct LDAP bind)."""
    if settings.demo_mode:
        return [{"username": u, **m} for u, m in DEMO_USERS.items()]
    cn = _group_cn(settings.ldap_required_group)
    if not cn:
        return []
    res = ldap_group_members(cn)
    return [{"username": m["username"], "display_name": m.get("display_name") or m["username"],
             "email": ""} for m in (res.get("members") or []) if m.get("username")]


_LDAP_GROUP_CACHE: dict = {}  # cn -> {"at": ts, "value": {...}}
_LDAP_GROUP_TTL = 3600

# demo LDAP groups referenced by the demo ADO project descriptions ([TEAM])
_DEMO_LDAP_GROUPS = {
    "platform-devs": ["Alice Nasr", "Bob Farid", "Carol Adel", "Dave Samir"],
    "control-owners": ["Alice Nasr", "Bob Farid"],
    "research-team": ["Carol Adel"],
    "platform-qa": ["Grace Hany", "Hesham Aly"],
    "sre-core": ["Dave Samir", "Omar Waleed", "Alice Nasr"],
}

# [TEAM] members are resolved by running an asset the user's cloned Engine repo
# ships: scripts/Tools/LDAP/getTeamMembersCN.sh <team> prints that team's members.
# The script (a) sources a .prd profile via `. $HOME/.prd` — where .prd is the
# file at the Engine repo ROOT, expected under the runner's REAL $HOME — and
# (b) does work relative to the current directory, so it must run from INSIDE
# the Engine repo. So we copy <engine>/.prd to $HOME/.prd and run with cwd set
# to the repo (leaving $HOME untouched).
_TEAM_SCRIPT_FALLBACK = "scripts/Tools/LDAP/getTeamMembersCN.sh"
_TEAM_SCRIPT_TIMEOUT = 60


def _team_script_candidates() -> list[str]:
    """The configured script first (QO_TEAM_MEMBERS_SCRIPT, default
    scripts/Tools/LDAP/getTeamMembers.sh), then the legacy CN variant."""
    cfg = (settings.team_members_script or "").strip().lstrip("/")
    out = [c for c in (cfg, _TEAM_SCRIPT_FALLBACK) if c]
    return list(dict.fromkeys(out))


_TEAM_SCRIPT_REL = _team_script_candidates()[0]   # label used in notes / status


def _team_script_path(d):
    """First candidate script that exists inside the cloned Engine repo."""
    for rel in _team_script_candidates():
        if (d / rel).exists():
            return d / rel
    return None


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
    sp = _team_script_path(d)
    row["script_present"] = sp is not None
    if sp is not None:
        row["script"] = str(sp.relative_to(d))
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
    script = _team_script_path(d)
    if script is None:
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
        if profile and (settings.login_without_password
                        or password == settings.demo_password):
            return {"username": username, "role": role_for(username), **profile}
        return None
    # live mode: demo accounts must never work
    if not settings.ldap_url:
        raise RuntimeError("demo mode is off but LDAP_URL is not configured — no way to log in")
    if not password and not settings.login_without_password:
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
