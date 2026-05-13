"""
Jira Permission Schemes — Mass Grant / Revoke Console

A faster, smarter alternative to the native Jira DC permission-scheme UI:
  • Browse — see every scheme's permissions × holders in a single matrix
  • Grant in bulk — one holder → N permissions × M schemes in one operation
  • Revoke in bulk — pivot to "what does user X have everywhere?" and tick
  • Copy holder — clone all grants from holder A to holder B
  • Paste-many — fan the same op out across pasted usernames or groups
  • Dry-run preview with green/red diff before any change is committed
  • Session audit log of every write made through this page

Backend: Jira DC REST API v2. Auth comes from Vault via the project's
JiraAPI helper (snippet provided by the user); a local-dev fallback to
JIRA_HOST / JIRA_USER / JIRA_PASSWORD env vars is included for testing.
"""

from __future__ import annotations

import os
import json
import time
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

import requests
from requests.auth import HTTPBasicAuth
import streamlit as st

# Project-internal modules — present in the production env, absent locally.
# Fall through to a basic-auth-from-env fallback if missing so the page is
# still runnable from a dev box.
try:
    from utils.vault import VaultClient  # type: ignore
except ImportError:
    VaultClient = None  # type: ignore

try:
    from utils.decorators import get_logger  # type: ignore
    logger = get_logger()
except ImportError:
    import logging
    logger = logging.getLogger("jira_permissions")
    if not logger.handlers:
        logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# JiraAPI — user's canonical snippet (Vault-backed basic auth), with a local
# env-var fallback when VaultClient isn't importable.
# ---------------------------------------------------------------------------
class JiraAPI:
    def __init__(self):
        if VaultClient is not None:
            vc = VaultClient()
            self.config = vc.read_all_nested_secrets("jira")
            self.base_url = self.config["host"]
            self.auth = HTTPBasicAuth(self.config["username"], self.config["password"])
        else:
            host = os.environ.get("JIRA_HOST")
            user = os.environ.get("JIRA_USER")
            pwd = os.environ.get("JIRA_PASSWORD") or os.environ.get("JIRA_TOKEN")
            if not (host and user and pwd):
                raise RuntimeError(
                    "JiraAPI: Vault unavailable and JIRA_HOST/JIRA_USER/"
                    "JIRA_PASSWORD env vars are not all set."
                )
            self.config = {"host": host, "username": user, "password": pwd}
            self.base_url = host
            self.auth = HTTPBasicAuth(user, pwd)

    def request(self, method, url, **kwargs):
        try:
            response = requests.request(method, url, auth=self.auth, timeout=kwargs.pop("timeout", 30), **kwargs)
            response.raise_for_status()
            return response.json() if response.text else {}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error in {method} request to {url}: {e}")
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Page config + styling. Palette borrowed from cicd_dashboard.py so this
# page reads as part of the same surface.
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Jira Permission Schemes",
    layout="wide",
    initial_sidebar_state="collapsed",
)

CUSTOM_CSS = """
<style>
:root {
    --jp-surface:   #ffffff;
    --jp-surface2:  #f7f8fb;
    --jp-border:    #e3e6ee;
    --jp-border-hi: #c7cce0;
    --jp-text:      #1a1d2e;
    --jp-text-dim:  #4a5068;
    --jp-text-mute: #8890a4;
    --jp-accent:    #0052cc;     /* Atlassian blue */
    --jp-accent-lt: #deebff;
    --jp-green:     #059669;
    --jp-green-lt:  #d1fae5;
    --jp-red:       #dc2626;
    --jp-red-lt:    #fee2e2;
    --jp-amber:     #d97706;
    --jp-amber-lt:  #fef3c7;
    --jp-mono:      'SF Mono', 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
}

.block-container { padding-top: 1rem; padding-bottom: 3rem; max-width: 1500px; }

h1, h2, h3, h4 { color: var(--jp-text); letter-spacing: -.01em; }

.jp-header {
    display: flex; align-items: baseline; gap: .8rem;
    padding-bottom: .4rem; margin-bottom: 1rem;
    border-bottom: 1px solid var(--jp-border);
}
.jp-header h1 { margin: 0; font-size: 1.55rem; font-weight: 600; }
.jp-header .jp-host {
    font-family: var(--jp-mono); font-size: .78rem; color: var(--jp-text-mute);
    padding: .15rem .5rem; background: var(--jp-surface2);
    border: 1px solid var(--jp-border); border-radius: 4px;
}

.jp-pill {
    display: inline-block; padding: .14rem .55rem; border-radius: 999px;
    font-size: .72rem; font-weight: 500; line-height: 1.3;
    background: var(--jp-surface2); color: var(--jp-text-dim);
    border: 1px solid var(--jp-border); margin-right: .25rem;
}
.jp-pill.jp-grant  { background: var(--jp-green-lt);  color: var(--jp-green); border-color: #a7f3d0; }
.jp-pill.jp-revoke { background: var(--jp-red-lt);    color: var(--jp-red);   border-color: #fecaca; }
.jp-pill.jp-warn   { background: var(--jp-amber-lt);  color: var(--jp-amber); border-color: #fde68a; }
.jp-pill.jp-info   { background: var(--jp-accent-lt); color: var(--jp-accent); border-color: #b3d4ff; }

.jp-card {
    background: var(--jp-surface); border: 1px solid var(--jp-border);
    border-radius: 10px; padding: 1rem 1.1rem; margin-bottom: .8rem;
}
.jp-card-head {
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: .5rem;
}
.jp-card-head .jp-title { font-weight: 600; font-size: 1.02rem; color: var(--jp-text); }
.jp-card-head .jp-sub   { font-size: .8rem; color: var(--jp-text-mute); }

.jp-grant-row {
    display: flex; align-items: center; gap: .6rem;
    padding: .35rem .55rem; border-radius: 6px;
    background: var(--jp-surface2); margin-bottom: .25rem;
    font-size: .85rem;
}
.jp-grant-row .jp-perm  { font-family: var(--jp-mono); font-size: .75rem; color: var(--jp-accent); min-width: 220px; }
.jp-grant-row .jp-holder { color: var(--jp-text-dim); }
.jp-grant-row.jp-add    { background: var(--jp-green-lt);  border-left: 3px solid var(--jp-green); }
.jp-grant-row.jp-del    { background: var(--jp-red-lt);    border-left: 3px solid var(--jp-red); }

.jp-diff-num {
    font-family: var(--jp-mono); font-size: 1.6rem; font-weight: 600;
    line-height: 1.1; margin-bottom: 0;
}
.jp-diff-num.jp-add { color: var(--jp-green); }
.jp-diff-num.jp-del { color: var(--jp-red); }

.jp-audit-row {
    display: grid;
    grid-template-columns: 140px 70px 90px 1fr 80px;
    gap: .6rem; padding: .35rem .55rem;
    font-size: .8rem; border-bottom: 1px dashed var(--jp-border);
    align-items: center;
}
.jp-audit-row .jp-ts { font-family: var(--jp-mono); color: var(--jp-text-mute); }
.jp-audit-row .jp-status-ok   { color: var(--jp-green); font-weight: 600; }
.jp-audit-row .jp-status-err  { color: var(--jp-red);   font-weight: 600; }

.jp-empty {
    text-align: center; padding: 2.5rem 1rem; color: var(--jp-text-mute);
    background: var(--jp-surface2); border: 1px dashed var(--jp-border);
    border-radius: 10px;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Admin gate. Canonical role source is st.session_state.user_roles (dict).
# Permission-scheme writes can damage every project on the instance — only
# admins reach the write paths. Non-admins get a read-only banner.
# ---------------------------------------------------------------------------
def _is_admin() -> bool:
    roles = st.session_state.get("user_roles") or {}
    if isinstance(roles, dict):
        return "admin" in {str(k).strip().lower() for k in roles.keys()}
    if isinstance(roles, (list, tuple, set)):
        return "admin" in {str(r).strip().lower() for r in roles}
    return False

# Allow a local-dev override so the page is usable outside the auth shell.
_LOCAL_DEV_BYPASS = os.environ.get("JIRA_PERMS_DEV_BYPASS") == "1"
ADMIN = _is_admin() or _LOCAL_DEV_BYPASS


# ---------------------------------------------------------------------------
# API singleton + thin typed wrappers around the user's request(). The read
# helpers are cached; writes use a separate path that surfaces the server's
# error body (which the JiraAPI.request wrapper swallows via raise_for_status).
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _api() -> JiraAPI:
    return JiraAPI()


def _full(path: str) -> str:
    base = _api().base_url.rstrip("/")
    return f"{base}{path}" if path.startswith("/") else f"{base}/{path}"


def _jira_write(method: str, path: str, **kwargs) -> tuple[bool, dict, int | None]:
    """Write-path call: returns (ok, body, status) and never raises. We need
    the response body on failures to surface the *actual* Jira error
    (\"permission already exists\", \"unknown holder\") rather than the bare
    status line that raise_for_status produces."""
    api = _api()
    url = _full(path)
    try:
        r = requests.request(method, url, auth=api.auth, timeout=kwargs.pop("timeout", 30), **kwargs)
        try:
            body = r.json() if r.text else {}
        except ValueError:
            body = {"raw": r.text}
        ok = 200 <= r.status_code < 300
        return ok, body, r.status_code
    except requests.exceptions.RequestException as e:
        logger.error(f"Jira write error {method} {url}: {e}")
        return False, {"error": str(e)}, None


# --- Read paths (cached) ---------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_schemes() -> list[dict]:
    """List every permission scheme on the instance (no grant detail)."""
    res = _api().request("GET", _full("/rest/api/2/permissionscheme"))
    if isinstance(res, dict) and "error" in res:
        st.error(f"Failed to list permission schemes: {res['error']}")
        return []
    return list((res or {}).get("permissionSchemes") or [])


@st.cache_data(ttl=300, show_spinner=False)
def fetch_scheme_detail(scheme_id: int) -> dict:
    """Fetch a scheme with every grant expanded — users, groups, project
    roles, application roles, fields. Returns the raw Jira shape."""
    res = _api().request(
        "GET",
        _full(f"/rest/api/2/permissionscheme/{int(scheme_id)}"),
        params={"expand": "permissions,user,group,projectRole,field,all"},
    )
    if isinstance(res, dict) and "error" in res:
        st.error(f"Failed to fetch scheme {scheme_id}: {res['error']}")
        return {}
    return res or {}


@st.cache_data(ttl=900, show_spinner=False)
def fetch_all_permission_keys() -> list[dict]:
    """Every permission key the instance recognises (ADMINISTER,
    CREATE_ISSUES, …) with its display name + description."""
    res = _api().request("GET", _full("/rest/api/2/permissions"))
    if isinstance(res, dict) and "error" in res:
        st.error(f"Failed to list permissions: {res['error']}")
        return []
    perms = (res or {}).get("permissions") or {}
    out = []
    for key, meta in perms.items():
        out.append({
            "key": key,
            "name": meta.get("name") or key,
            "type": meta.get("type") or "",
            "description": meta.get("description") or "",
        })
    out.sort(key=lambda p: p["name"].lower())
    return out


@st.cache_data(ttl=300, show_spinner=False)
def fetch_projects_for_scheme(scheme_id: int) -> list[dict]:
    """Walk projects and keep those bound to this scheme. Jira DC has no
    direct scheme→projects endpoint, so we paginate /project and check each
    project's permissionscheme link. Cached aggressively."""
    out: list[dict] = []
    start = 0
    page = 50
    while True:
        res = _api().request(
            "GET",
            _full("/rest/api/2/project/search"),
            params={"startAt": start, "maxResults": page, "expand": "lead"},
        )
        if isinstance(res, dict) and "error" in res:
            # Fall back to /rest/api/2/project (older DCs lack /project/search)
            res2 = _api().request("GET", _full("/rest/api/2/project"))
            if isinstance(res2, dict) and "error" in res2:
                return []
            projects = res2 if isinstance(res2, list) else []
            for p in projects:
                ps = _api().request(
                    "GET",
                    _full(f"/rest/api/2/project/{p['key']}/permissionscheme"),
                )
                if isinstance(ps, dict) and int(ps.get("id") or -1) == int(scheme_id):
                    out.append({"key": p["key"], "name": p.get("name") or p["key"]})
            return out
        values = res.get("values") or []
        if not values:
            break
        for p in values:
            ps = _api().request(
                "GET",
                _full(f"/rest/api/2/project/{p['key']}/permissionscheme"),
            )
            if isinstance(ps, dict) and int(ps.get("id") or -1) == int(scheme_id):
                out.append({"key": p["key"], "name": p.get("name") or p["key"]})
        if res.get("isLast") or len(values) < page:
            break
        start += page
    return out


@st.cache_data(ttl=60, show_spinner=False)
def search_users(query: str, max_results: int = 30) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    res = _api().request(
        "GET",
        _full("/rest/api/2/user/picker"),
        params={"query": q, "maxResults": max_results, "showAvatar": False},
    )
    if isinstance(res, dict) and "error" in res:
        return []
    users = (res or {}).get("users") or []
    out = []
    for u in users:
        out.append({
            "name": u.get("name") or u.get("key") or "",
            "key": u.get("key") or u.get("name") or "",
            "display": u.get("displayName") or u.get("name") or "",
            "email": u.get("emailAddress") or "",
        })
    return out


@st.cache_data(ttl=60, show_spinner=False)
def search_groups(query: str, max_results: int = 30) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    res = _api().request(
        "GET",
        _full("/rest/api/2/groups/picker"),
        params={"query": q, "maxResults": max_results},
    )
    if isinstance(res, dict) and "error" in res:
        return []
    groups = (res or {}).get("groups") or []
    return [{"name": g.get("name", ""), "html": g.get("html", "")} for g in groups]


def _invalidate_scheme_cache(scheme_id: int | None = None) -> None:
    """Drop cached reads after a write. We invalidate aggressively — better
    a stale-by-a-second refetch than the UI lying about what's live."""
    fetch_scheme_detail.clear()
    fetch_all_schemes.clear()


# ---------------------------------------------------------------------------
# Domain model — internal shape used by the UI for pending ops, diffs, and
# the audit log. Decouples from Jira's wire shape so we can render uniformly.
# ---------------------------------------------------------------------------
HOLDER_TYPES = ("user", "group")  # this page is intentionally scoped to these


@dataclass
class Grant:
    scheme_id: int
    scheme_name: str
    permission_id: int           # Jira's internal grant id — used to DELETE
    permission_key: str          # e.g. CREATE_ISSUES
    holder_type: str             # user | group | projectRole | …
    holder_param: str            # username / group name / role id / …
    holder_display: str          # human label

    def matches_holder(self, htype: str, hparam: str) -> bool:
        return self.holder_type == htype and self.holder_param == hparam


@dataclass
class PendingOp:
    action: str                  # "grant" | "revoke"
    scheme_id: int
    scheme_name: str
    permission_key: str
    holder_type: str
    holder_param: str
    holder_display: str
    permission_id: int | None = None   # set for revoke; Jira's grant id

    def signature(self) -> tuple:
        return (self.action, self.scheme_id, self.permission_key, self.holder_type, self.holder_param)


def _parse_grants(scheme: dict) -> list[Grant]:
    out: list[Grant] = []
    sid = int(scheme.get("id") or 0)
    sname = str(scheme.get("name") or "")
    for p in scheme.get("permissions") or []:
        holder = p.get("holder") or {}
        htype = str(holder.get("type") or "")
        hparam = str(holder.get("parameter") or "")
        # Friendly display name — fall back through the expansion shapes Jira
        # uses for user / group / projectRole.
        display = hparam or "—"
        if htype == "user" and isinstance(holder.get("user"), dict):
            display = holder["user"].get("displayName") or hparam
        elif htype == "group" and isinstance(holder.get("group"), dict):
            display = holder["group"].get("name") or hparam
        elif htype == "projectRole" and isinstance(holder.get("projectRole"), dict):
            display = holder["projectRole"].get("name") or hparam
        out.append(Grant(
            scheme_id=sid,
            scheme_name=sname,
            permission_id=int(p.get("id") or 0),
            permission_key=str(p.get("permission") or ""),
            holder_type=htype,
            holder_param=hparam,
            holder_display=str(display),
        ))
    return out


# ---------------------------------------------------------------------------
# Session-state helpers — pending ops queue + audit log live here so they
# survive Streamlit reruns until executed or explicitly cleared.
# ---------------------------------------------------------------------------
def _ss_init():
    st.session_state.setdefault("jp_pending", [])      # list[PendingOp as dict]
    st.session_state.setdefault("jp_audit", [])        # list[dict]
    st.session_state.setdefault("jp_holder", None)     # current target {type, param, display}

_ss_init()


def _queue(op: PendingOp) -> bool:
    """Append a pending op if not already queued. Returns True if queued."""
    sig = op.signature()
    for existing in st.session_state["jp_pending"]:
        if tuple(existing["_sig"]) == sig:
            return False
    rec = asdict(op)
    rec["_sig"] = list(sig)
    st.session_state["jp_pending"].append(rec)
    return True


def _clear_pending():
    st.session_state["jp_pending"] = []


def _record_audit(op: PendingOp, ok: bool, status: int | None, body: dict):
    st.session_state["jp_audit"].append({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "action": op.action,
        "scheme_id": op.scheme_id,
        "scheme_name": op.scheme_name,
        "permission_key": op.permission_key,
        "holder_type": op.holder_type,
        "holder_param": op.holder_param,
        "holder_display": op.holder_display,
        "ok": bool(ok),
        "status": status,
        "error": None if ok else (body.get("errorMessages") or body.get("error") or body.get("raw") or body),
    })


def _execute_pending() -> tuple[int, int]:
    """Walk the pending queue, POST/DELETE each, record audit, clear queue.
    Returns (ok_count, fail_count)."""
    ok_count = 0
    fail_count = 0
    touched_schemes: set[int] = set()
    for raw in list(st.session_state["jp_pending"]):
        op = PendingOp(
            action=raw["action"],
            scheme_id=raw["scheme_id"],
            scheme_name=raw["scheme_name"],
            permission_key=raw["permission_key"],
            holder_type=raw["holder_type"],
            holder_param=raw["holder_param"],
            holder_display=raw["holder_display"],
            permission_id=raw.get("permission_id"),
        )
        if op.action == "grant":
            ok, body, status = _jira_write(
                "POST",
                f"/rest/api/2/permissionscheme/{op.scheme_id}/permission",
                json={
                    "holder": {"type": op.holder_type, "parameter": op.holder_param},
                    "permission": op.permission_key,
                },
            )
        elif op.action == "revoke":
            if not op.permission_id:
                ok, body, status = False, {"error": "missing permission_id for revoke"}, None
            else:
                ok, body, status = _jira_write(
                    "DELETE",
                    f"/rest/api/2/permissionscheme/{op.scheme_id}/permission/{op.permission_id}",
                )
        else:
            ok, body, status = False, {"error": f"unknown action {op.action}"}, None
        _record_audit(op, ok, status, body if isinstance(body, dict) else {"raw": body})
        if ok:
            ok_count += 1
        else:
            fail_count += 1
        touched_schemes.add(op.scheme_id)
    for sid in touched_schemes:
        _invalidate_scheme_cache(sid)
    _clear_pending()
    return ok_count, fail_count


# ---------------------------------------------------------------------------
# Holder picker — used by every write tab. Returns {"type", "param",
# "display"} or None. Sticky across reruns via session_state["jp_holder"].
# ---------------------------------------------------------------------------
def holder_picker(key_prefix: str, *, label: str = "Target holder") -> dict | None:
    cols = st.columns([1, 3])
    with cols[0]:
        htype = st.selectbox(
            "Type",
            HOLDER_TYPES,
            key=f"{key_prefix}_type",
            format_func=lambda x: {"user": "👤 User", "group": "👥 Group"}[x],
        )
    with cols[1]:
        query = st.text_input(
            label,
            key=f"{key_prefix}_query",
            placeholder=f"Search by name… (min 2 chars)",
        )
    if not query or len(query.strip()) < 2:
        return None

    if htype == "user":
        results = search_users(query)
        labels = {f"{u['display']}  ⟨{u['name']}⟩{('  · ' + u['email']) if u['email'] else ''}": u for u in results}
    else:
        results = search_groups(query)
        labels = {g["name"]: g for g in results}

    if not labels:
        st.caption(f"No matching {htype}s.")
        return None
    pick = st.selectbox("Select", list(labels.keys()), key=f"{key_prefix}_pick")
    chosen = labels[pick]
    if htype == "user":
        return {"type": "user", "param": chosen["name"], "display": chosen["display"] or chosen["name"]}
    return {"type": "group", "param": chosen["name"], "display": chosen["name"]}


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
try:
    _host = _api().base_url
except Exception as e:
    _host = "(no connection)"
    st.error(f"Jira API initialization failed: {e}")
    st.stop()

st.markdown(
    f"""
<div class="jp-header">
  <h1>🛡️ Jira Permission Schemes</h1>
  <span class="jp-host">{_host}</span>
  <span style="margin-left:auto;font-size:.78rem;color:var(--jp-text-mute);">
    Mass grant / revoke · cross-scheme search · dry-run preview
  </span>
</div>
""",
    unsafe_allow_html=True,
)

if not ADMIN:
    st.warning(
        "🔒 This page is **admin-only** because permission-scheme writes "
        "affect every project bound to the scheme. You'll see browse / "
        "search tabs in read-only mode."
    )


# ---------------------------------------------------------------------------
# Sidebar — minimal rail (refresh + connection status only, per project's
# rail UX philosophy: don't re-add selectors that belong in the main pane).
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Connection")
    st.caption(f"`{_host}`")
    if st.button("🔄 Refresh all caches", use_container_width=True):
        fetch_all_schemes.clear()
        fetch_scheme_detail.clear()
        fetch_all_permission_keys.clear()
        fetch_projects_for_scheme.clear()
        search_users.clear()
        search_groups.clear()
        st.success("Caches cleared.")
        st.rerun()
    pending_n = len(st.session_state["jp_pending"])
    audit_n = len(st.session_state["jp_audit"])
    st.markdown(
        f"<div style='margin-top:1rem;font-size:.85rem;color:var(--jp-text-dim);'>"
        f"Pending ops: <b>{pending_n}</b><br>"
        f"Session audit log: <b>{audit_n}</b>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if pending_n and st.button("Clear pending queue", use_container_width=True):
        _clear_pending()
        st.rerun()


# ---------------------------------------------------------------------------
# Load core data once per rerun (cheap with caching).
# ---------------------------------------------------------------------------
schemes = fetch_all_schemes()
schemes_by_id: dict[int, dict] = {int(s["id"]): s for s in schemes if s.get("id") is not None}
perm_catalog = fetch_all_permission_keys()
perm_keys_sorted = [p["key"] for p in perm_catalog]
perm_name_by_key = {p["key"]: p["name"] for p in perm_catalog}
perm_desc_by_key = {p["key"]: p["description"] for p in perm_catalog}

if not schemes:
    st.markdown(
        '<div class="jp-empty">No permission schemes returned. Check your Vault config / Jira reachability.</div>',
        unsafe_allow_html=True,
    )
    st.stop()


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_browse, tab_grant, tab_revoke, tab_copy, tab_search, tab_audit = st.tabs([
    "🔭 Browse",
    "➕ Grant in bulk",
    "➖ Revoke in bulk",
    "⇄ Copy / Move holder",
    "🔎 Where is this holder?",
    "📋 Audit log",
])


# ===========================================================================
# Tab: Browse — every scheme, every grant, with permission-by-holder matrix
# ===========================================================================
with tab_browse:
    st.markdown("##### Browse permission schemes")
    st.caption(
        "Pick a scheme to see its full grant table grouped by permission. "
        "Use the project lookup to confirm blast radius before editing."
    )

    name_to_id = {f"{s['name']}  ⟨id {s['id']}⟩": int(s["id"]) for s in schemes}
    pick = st.selectbox("Scheme", list(name_to_id.keys()), key="browse_pick")
    sid = name_to_id[pick]

    scheme = fetch_scheme_detail(sid)
    if not scheme:
        st.warning("Could not load scheme detail.")
    else:
        grants = _parse_grants(scheme)
        desc = scheme.get("description") or ""
        c1, c2, c3 = st.columns([2, 1, 1])
        c1.markdown(f"**{scheme.get('name')}** &nbsp; <span class='jp-pill jp-info'>id {sid}</span>", unsafe_allow_html=True)
        c1.caption(desc or "_(no description)_")
        c2.metric("Total grants", len(grants))
        c3.metric("Distinct permissions", len({g.permission_key for g in grants}))

        with st.expander(f"Projects bound to this scheme", expanded=False):
            with st.spinner("Walking project list…"):
                bound = fetch_projects_for_scheme(sid)
            if not bound:
                st.caption("No projects use this scheme (or lookup failed).")
            else:
                st.markdown(
                    " ".join(
                        f"<span class='jp-pill jp-info'>{p['key']} · {p['name']}</span>"
                        for p in bound
                    ),
                    unsafe_allow_html=True,
                )
                st.caption(f"{len(bound)} project(s)")

        # Filters
        f1, f2, f3 = st.columns([2, 2, 1])
        with f1:
            perm_filter = st.multiselect(
                "Filter permissions",
                sorted({g.permission_key for g in grants}),
                key=f"browse_pf_{sid}",
            )
        with f2:
            holder_filter = st.text_input(
                "Filter holder (substring)",
                key=f"browse_hf_{sid}",
                placeholder="username, group name, role…",
            )
        with f3:
            htype_filter = st.multiselect(
                "Holder type",
                sorted({g.holder_type for g in grants}),
                key=f"browse_htf_{sid}",
            )

        def _passes(g: Grant) -> bool:
            if perm_filter and g.permission_key not in perm_filter:
                return False
            if htype_filter and g.holder_type not in htype_filter:
                return False
            if holder_filter:
                hl = holder_filter.lower()
                if hl not in g.holder_param.lower() and hl not in g.holder_display.lower():
                    return False
            return True

        visible = [g for g in grants if _passes(g)]
        # Group by permission
        by_perm: dict[str, list[Grant]] = {}
        for g in visible:
            by_perm.setdefault(g.permission_key, []).append(g)

        st.markdown(f"Showing **{len(visible)}** of {len(grants)} grants across **{len(by_perm)}** permissions.")

        for pkey in sorted(by_perm.keys()):
            holders = by_perm[pkey]
            with st.container():
                st.markdown(
                    f"<div class='jp-card'><div class='jp-card-head'>"
                    f"<div><span class='jp-title'>{perm_name_by_key.get(pkey, pkey)}</span>"
                    f"  <span class='jp-pill'>{pkey}</span></div>"
                    f"<div class='jp-sub'>{len(holders)} holder(s)</div></div>",
                    unsafe_allow_html=True,
                )
                if perm_desc_by_key.get(pkey):
                    st.caption(perm_desc_by_key[pkey])
                rows = []
                for g in holders:
                    badge = {
                        "user": "👤", "group": "👥", "projectRole": "🎭",
                        "applicationRole": "🧩", "assignee": "📌", "reporter": "🗣️",
                        "currentAssignee": "📌", "projectLead": "👑",
                        "currentUser": "🙋", "anyone": "🌐",
                    }.get(g.holder_type, "•")
                    rows.append(
                        f"<div class='jp-grant-row'>"
                        f"<span class='jp-perm'>{g.holder_type}</span>"
                        f"<span class='jp-holder'>{badge} {g.holder_display} "
                        f"<span style='color:var(--jp-text-mute);'>⟨{g.holder_param or '—'}⟩</span></span>"
                        f"</div>"
                    )
                st.markdown("".join(rows), unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)


# ===========================================================================
# Tab: Grant in bulk — one or more holders × N permissions × M schemes
# ===========================================================================
with tab_grant:
    st.markdown("##### Grant permissions in bulk")
    st.caption(
        "Pick one holder (or paste many), select the permissions, select the "
        "schemes. Every combination is queued as a pending op; nothing is "
        "applied until you confirm in the preview pane."
    )
    if not ADMIN:
        st.info("Read-only mode — admin role required to queue writes.")

    mode = st.radio(
        "Holder input",
        ["Single (search)", "Paste many"],
        horizontal=True,
        key="grant_mode",
        disabled=not ADMIN,
    )

    chosen_holders: list[dict] = []
    if mode == "Single (search)":
        h = holder_picker("grant_single", label="Holder to grant to")
        if h:
            chosen_holders = [h]
    else:
        c1, c2 = st.columns([1, 3])
        with c1:
            paste_type = st.selectbox(
                "Type",
                HOLDER_TYPES,
                format_func=lambda x: {"user": "👤 Users", "group": "👥 Groups"}[x],
                key="grant_paste_type",
            )
        with c2:
            pasted = st.text_area(
                "One name per line (username for users, group name for groups)",
                key="grant_paste_text",
                height=110,
                placeholder="jdoe\nasmith\nfgarcia",
            )
        lines = [ln.strip() for ln in (pasted or "").splitlines() if ln.strip()]
        # Dedupe, preserve order
        seen = set()
        for ln in lines:
            if ln in seen:
                continue
            seen.add(ln)
            chosen_holders.append({"type": paste_type, "param": ln, "display": ln})
        if chosen_holders:
            st.markdown(
                " ".join(f"<span class='jp-pill'>{h['display']}</span>" for h in chosen_holders),
                unsafe_allow_html=True,
            )
            st.caption(f"{len(chosen_holders)} holder(s) parsed.")

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        perm_sel = st.multiselect(
            "Permissions to grant",
            perm_keys_sorted,
            format_func=lambda k: f"{perm_name_by_key.get(k, k)}  ⟨{k}⟩",
            key="grant_perms",
            help="Each picks a permission *key* — Jira translates these into the right grant rows.",
        )
        if perm_sel:
            with st.expander("Permission details", expanded=False):
                for k in perm_sel:
                    st.markdown(f"**{perm_name_by_key.get(k, k)}** `{k}` — {perm_desc_by_key.get(k, '')}")
    with c2:
        all_scheme_labels = [f"{s['name']}  ⟨id {s['id']}⟩" for s in schemes]
        scheme_sel = st.multiselect(
            "Schemes to apply to",
            all_scheme_labels,
            key="grant_schemes",
            help="Every selected scheme gets the same grant set.",
        )
        if st.button("Select all schemes", key="grant_all_schemes"):
            st.session_state["grant_schemes"] = all_scheme_labels
            st.rerun()

    # Resolve selected scheme ids
    selected_sids: list[int] = []
    for lbl in scheme_sel or []:
        m = re.search(r"⟨id (\d+)⟩", lbl)
        if m:
            selected_sids.append(int(m.group(1)))

    can_queue = ADMIN and chosen_holders and perm_sel and selected_sids
    op_count = len(chosen_holders) * len(perm_sel or []) * len(selected_sids or [])
    st.markdown(
        f"<div class='jp-card'>"
        f"<b>Plan:</b> {len(chosen_holders)} holder(s) × {len(perm_sel or [])} permission(s) × {len(selected_sids or [])} scheme(s) "
        f"= <span class='jp-pill jp-grant'>{op_count} grant(s)</span> queued"
        f"</div>",
        unsafe_allow_html=True,
    )

    skip_existing = st.checkbox(
        "Skip grants that already exist (idempotent)",
        value=True,
        key="grant_skip_existing",
        help="Fetches each scheme and drops grants already present.",
    )

    if st.button("➕ Queue grants for preview", type="primary", disabled=not can_queue):
        queued = 0
        skipped_existing = 0
        already_pending = 0

        # Pre-load each target scheme once if we need to dedupe.
        existing_keysets: dict[int, set[tuple[str, str, str]]] = {}
        if skip_existing:
            for sid in selected_sids:
                det = fetch_scheme_detail(sid)
                existing_keysets[sid] = {
                    (g.permission_key, g.holder_type, g.holder_param)
                    for g in _parse_grants(det)
                }

        for h in chosen_holders:
            for sid in selected_sids:
                sname = schemes_by_id.get(sid, {}).get("name", str(sid))
                for pkey in perm_sel:
                    if skip_existing and (pkey, h["type"], h["param"]) in existing_keysets.get(sid, set()):
                        skipped_existing += 1
                        continue
                    op = PendingOp(
                        action="grant",
                        scheme_id=sid,
                        scheme_name=sname,
                        permission_key=pkey,
                        holder_type=h["type"],
                        holder_param=h["param"],
                        holder_display=h["display"],
                    )
                    if _queue(op):
                        queued += 1
                    else:
                        already_pending += 1
        msg = f"Queued **{queued}** new grant op(s)."
        if skipped_existing:
            msg += f" Skipped {skipped_existing} already present."
        if already_pending:
            msg += f" {already_pending} were already in the pending queue."
        st.success(msg)


# ===========================================================================
# Tab: Revoke in bulk — pick a holder, see every grant they have, tick rows
# ===========================================================================
with tab_revoke:
    st.markdown("##### Revoke permissions in bulk")
    st.caption(
        "Pick a holder. We'll scan every scheme and surface every grant "
        "that holder has — tick the ones to revoke. Cross-scheme revoke "
        "in one operation."
    )
    if not ADMIN:
        st.info("Read-only mode — admin role required to queue writes.")

    h = holder_picker("revoke_single", label="Holder to revoke from")

    if h:
        # Scan all schemes for this holder
        progress = st.progress(0, text="Scanning schemes…")
        matching: list[Grant] = []
        for i, s in enumerate(schemes):
            det = fetch_scheme_detail(int(s["id"]))
            for g in _parse_grants(det):
                if g.matches_holder(h["type"], h["param"]):
                    matching.append(g)
            progress.progress((i + 1) / max(len(schemes), 1), text=f"Scanned {i + 1}/{len(schemes)}")
        progress.empty()

        if not matching:
            st.markdown(
                f"<div class='jp-empty'>No grants found for "
                f"<b>{h['display']}</b> across {len(schemes)} schemes.</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"Found <span class='jp-pill jp-info'>{len(matching)}</span> grant(s) for "
                f"<b>{h['display']}</b> across "
                f"<span class='jp-pill jp-info'>{len({g.scheme_id for g in matching})}</span> scheme(s).",
                unsafe_allow_html=True,
            )

            # Group by scheme
            by_scheme: dict[int, list[Grant]] = {}
            for g in matching:
                by_scheme.setdefault(g.scheme_id, []).append(g)

            # Select-all helper
            sel_all = st.checkbox("Select all", key="revoke_sel_all")

            ticked: list[Grant] = []
            for sid in sorted(by_scheme.keys()):
                gs = by_scheme[sid]
                sname = gs[0].scheme_name
                st.markdown(f"**{sname}** &nbsp; <span class='jp-pill'>id {sid}</span>", unsafe_allow_html=True)
                for g in gs:
                    chk_key = f"revoke_chk_{sid}_{g.permission_id}"
                    default = sel_all
                    ticked_now = st.checkbox(
                        f"`{g.permission_key}`  —  {perm_name_by_key.get(g.permission_key, '')}",
                        value=default,
                        key=chk_key,
                        disabled=not ADMIN,
                    )
                    if ticked_now:
                        ticked.append(g)

            st.markdown("---")
            st.markdown(
                f"<div class='jp-card'>"
                f"<b>Plan:</b> revoke <span class='jp-pill jp-revoke'>{len(ticked)} grant(s)</span> "
                f"from <b>{h['display']}</b>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if st.button(
                "➖ Queue revokes for preview",
                type="primary",
                disabled=not (ADMIN and ticked),
            ):
                q = 0
                for g in ticked:
                    op = PendingOp(
                        action="revoke",
                        scheme_id=g.scheme_id,
                        scheme_name=g.scheme_name,
                        permission_key=g.permission_key,
                        holder_type=g.holder_type,
                        holder_param=g.holder_param,
                        holder_display=g.holder_display,
                        permission_id=g.permission_id,
                    )
                    if _queue(op):
                        q += 1
                st.success(f"Queued {q} revoke op(s).")


# ===========================================================================
# Tab: Copy / Move — clone all grants from holder A to holder B (optionally
# removing A in the same operation). Closest thing Jira DC's native UI has
# is "nothing" — this is a major time-saver.
# ===========================================================================
with tab_copy:
    st.markdown("##### Copy or move a holder's grants")
    st.caption(
        "Find every grant for holder **A** and queue the same grants for "
        "holder **B**. Optionally also queue revokes for **A** to perform a "
        "rename / hand-off. Nothing is committed until you confirm."
    )
    if not ADMIN:
        st.info("Read-only mode — admin role required to queue writes.")

    cA, cB = st.columns(2)
    with cA:
        st.markdown("**Source (A)**")
        h_src = holder_picker("copy_src", label="Copy FROM")
    with cB:
        st.markdown("**Destination (B)**")
        h_dst = holder_picker("copy_dst", label="Copy TO")

    move = st.checkbox(
        "Also revoke from source after copying (move instead of copy)",
        value=False,
        key="copy_move_flag",
        disabled=not ADMIN,
    )
    skip_existing = st.checkbox(
        "Skip destination grants that already exist",
        value=True,
        key="copy_skip_existing",
    )

    if h_src and h_dst:
        if (h_src["type"], h_src["param"]) == (h_dst["type"], h_dst["param"]):
            st.warning("Source and destination are the same — pick distinct holders.")
        else:
            with st.spinner("Scanning schemes for source's grants…"):
                src_grants: list[Grant] = []
                for s in schemes:
                    det = fetch_scheme_detail(int(s["id"]))
                    for g in _parse_grants(det):
                        if g.matches_holder(h_src["type"], h_src["param"]):
                            src_grants.append(g)

            if not src_grants:
                st.markdown(
                    f"<div class='jp-empty'>Source <b>{h_src['display']}</b> has no grants — "
                    f"nothing to copy.</div>",
                    unsafe_allow_html=True,
                )
            else:
                # Per-scheme destination keysets to dedupe
                dest_keysets: dict[int, set[tuple[str, str, str]]] = {}
                if skip_existing:
                    for s in {g.scheme_id for g in src_grants}:
                        det = fetch_scheme_detail(s)
                        dest_keysets[s] = {
                            (g.permission_key, g.holder_type, g.holder_param)
                            for g in _parse_grants(det)
                        }

                planned_grants = 0
                planned_revokes = 0
                for g in src_grants:
                    if skip_existing and (g.permission_key, h_dst["type"], h_dst["param"]) in dest_keysets.get(g.scheme_id, set()):
                        continue
                    planned_grants += 1
                if move:
                    planned_revokes = len(src_grants)

                st.markdown(
                    f"<div class='jp-card'>"
                    f"<b>Plan:</b> grant <span class='jp-pill jp-grant'>{planned_grants}</span> "
                    f"to <b>{h_dst['display']}</b>"
                    + (f" · revoke <span class='jp-pill jp-revoke'>{planned_revokes}</span> from <b>{h_src['display']}</b>" if move else "")
                    + f" · spans <span class='jp-pill jp-info'>{len({g.scheme_id for g in src_grants})}</span> scheme(s)"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                if st.button("⇄ Queue copy/move for preview", type="primary", disabled=not ADMIN):
                    queued = 0
                    for g in src_grants:
                        if not (skip_existing and (g.permission_key, h_dst["type"], h_dst["param"]) in dest_keysets.get(g.scheme_id, set())):
                            if _queue(PendingOp(
                                action="grant",
                                scheme_id=g.scheme_id,
                                scheme_name=g.scheme_name,
                                permission_key=g.permission_key,
                                holder_type=h_dst["type"],
                                holder_param=h_dst["param"],
                                holder_display=h_dst["display"],
                            )):
                                queued += 1
                        if move:
                            if _queue(PendingOp(
                                action="revoke",
                                scheme_id=g.scheme_id,
                                scheme_name=g.scheme_name,
                                permission_key=g.permission_key,
                                holder_type=g.holder_type,
                                holder_param=g.holder_param,
                                holder_display=g.holder_display,
                                permission_id=g.permission_id,
                            )):
                                queued += 1
                    st.success(f"Queued {queued} op(s).")


# ===========================================================================
# Tab: Where is this holder? — read-only cross-scheme search
# ===========================================================================
with tab_search:
    st.markdown("##### Locate a holder across every scheme")
    st.caption(
        "Pivots Jira's default \"permission → who has it\" into \"holder → "
        "where do they have what\". Useful for audits, off-boarding reviews, "
        "and answering \"why does this user see X?\"."
    )

    h = holder_picker("search_holder", label="Holder to locate")
    if h:
        with st.spinner("Scanning all schemes…"):
            hits: list[Grant] = []
            for s in schemes:
                det = fetch_scheme_detail(int(s["id"]))
                for g in _parse_grants(det):
                    if g.matches_holder(h["type"], h["param"]):
                        hits.append(g)

        if not hits:
            st.markdown(
                f"<div class='jp-empty'><b>{h['display']}</b> appears in no scheme.</div>",
                unsafe_allow_html=True,
            )
        else:
            by_scheme: dict[int, list[Grant]] = {}
            for g in hits:
                by_scheme.setdefault(g.scheme_id, []).append(g)

            c1, c2 = st.columns([1, 1])
            c1.metric("Schemes touched", len(by_scheme))
            c2.metric("Total grants", len(hits))

            for sid in sorted(by_scheme.keys()):
                gs = by_scheme[sid]
                with st.expander(
                    f"{gs[0].scheme_name}  —  {len(gs)} grant(s)",
                    expanded=False,
                ):
                    for g in sorted(gs, key=lambda x: x.permission_key):
                        st.markdown(
                            f"- `{g.permission_key}` — {perm_name_by_key.get(g.permission_key, g.permission_key)}"
                        )


# ===========================================================================
# Tab: Audit log — session-local trail of every API write
# ===========================================================================
with tab_audit:
    st.markdown("##### Session audit log")
    st.caption(
        "Every write this page has performed in the current session. Cleared "
        "when you reload the browser. Export to CSV / JSON for permanent record."
    )
    log = list(st.session_state["jp_audit"])
    if not log:
        st.markdown(
            "<div class='jp-empty'>No writes performed yet this session.</div>",
            unsafe_allow_html=True,
        )
    else:
        # Filter
        f1, f2, f3 = st.columns(3)
        with f1:
            f_action = st.multiselect("Action", ["grant", "revoke"], key="audit_f_action")
        with f2:
            f_status = st.selectbox("Status", ["all", "ok only", "errors only"], key="audit_f_status")
        with f3:
            f_text = st.text_input("Free-text filter", key="audit_f_text")

        def _row_ok(r: dict) -> bool:
            if f_action and r["action"] not in f_action:
                return False
            if f_status == "ok only" and not r["ok"]:
                return False
            if f_status == "errors only" and r["ok"]:
                return False
            if f_text:
                blob = json.dumps(r, default=str).lower()
                if f_text.lower() not in blob:
                    return False
            return True

        filtered = [r for r in log if _row_ok(r)]
        st.markdown(f"Showing **{len(filtered)}** of {len(log)} entries.")

        # Header row
        st.markdown(
            "<div class='jp-audit-row' style='font-weight:600;color:var(--jp-text-mute);'>"
            "<div>Timestamp (UTC)</div><div>Action</div><div>Status</div>"
            "<div>Detail</div><div>Scheme</div></div>",
            unsafe_allow_html=True,
        )
        for r in reversed(filtered):
            action_pill = f"<span class='jp-pill jp-{'grant' if r['action']=='grant' else 'revoke'}'>{r['action']}</span>"
            if r["ok"]:
                status_html = "<span class='jp-status-ok'>✓ ok</span>"
            else:
                status_html = f"<span class='jp-status-err'>✗ {r.get('status') or 'err'}</span>"
            detail = f"<code>{r['permission_key']}</code> · {r['holder_type']} <b>{r['holder_display']}</b> ⟨{r['holder_param']}⟩"
            if not r["ok"] and r.get("error"):
                err = str(r["error"])[:140]
                detail += f"<br><span style='color:var(--jp-red);font-size:.75rem;'>{err}</span>"
            st.markdown(
                f"<div class='jp-audit-row'>"
                f"<div class='jp-ts'>{r['ts']}</div>"
                f"<div>{action_pill}</div>"
                f"<div>{status_html}</div>"
                f"<div>{detail}</div>"
                f"<div>{r['scheme_name']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.markdown("---")
        c1, c2 = st.columns(2)
        c1.download_button(
            "⬇ Export JSON",
            data=json.dumps(log, indent=2, default=str),
            file_name=f"jira-permissions-audit-{int(time.time())}.json",
            mime="application/json",
            use_container_width=True,
        )
        # CSV
        import io, csv
        buf = io.StringIO()
        if log:
            w = csv.DictWriter(buf, fieldnames=list(log[0].keys()))
            w.writeheader()
            for r in log:
                row = {k: ("" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else v)) for k, v in r.items()}
                w.writerow(row)
        c2.download_button(
            "⬇ Export CSV",
            data=buf.getvalue(),
            file_name=f"jira-permissions-audit-{int(time.time())}.csv",
            mime="text/csv",
            use_container_width=True,
        )

        if st.button("Clear audit log", type="secondary"):
            st.session_state["jp_audit"] = []
            st.rerun()


# ===========================================================================
# Pending queue + confirmation gate — sticky panel below all tabs. Renders
# whenever there's something queued, so the user can't miss it.
# ===========================================================================
pending = st.session_state["jp_pending"]
if pending:
    st.markdown("---")
    st.markdown("## 🔍 Preview pending changes")

    grants = [p for p in pending if p["action"] == "grant"]
    revokes = [p for p in pending if p["action"] == "revoke"]

    c1, c2, c3 = st.columns([1, 1, 2])
    c1.markdown(
        f"<div class='jp-card'><div class='jp-diff-num jp-add'>+{len(grants)}</div>"
        f"<div style='color:var(--jp-text-mute);font-size:.8rem;'>grants to add</div></div>",
        unsafe_allow_html=True,
    )
    c2.markdown(
        f"<div class='jp-card'><div class='jp-diff-num jp-del'>-{len(revokes)}</div>"
        f"<div style='color:var(--jp-text-mute);font-size:.8rem;'>grants to remove</div></div>",
        unsafe_allow_html=True,
    )
    schemes_touched = sorted({p["scheme_id"] for p in pending})
    c3.markdown(
        f"<div class='jp-card'><div style='font-size:1.6rem;font-weight:600;'>{len(schemes_touched)}</div>"
        f"<div style='color:var(--jp-text-mute);font-size:.8rem;'>scheme(s) affected: "
        + ", ".join(schemes_by_id.get(sid, {}).get("name", str(sid)) for sid in schemes_touched[:8])
        + ("…" if len(schemes_touched) > 8 else "")
        + "</div></div>",
        unsafe_allow_html=True,
    )

    # Grouped diff by scheme
    pending_by_scheme: dict[int, list[dict]] = {}
    for p in pending:
        pending_by_scheme.setdefault(p["scheme_id"], []).append(p)

    for sid in sorted(pending_by_scheme.keys()):
        ops = pending_by_scheme[sid]
        sname = ops[0]["scheme_name"]
        with st.expander(f"{sname}  —  {len(ops)} op(s)", expanded=True):
            rows = []
            for p in ops:
                cls = "jp-add" if p["action"] == "grant" else "jp-del"
                sym = "+" if p["action"] == "grant" else "−"
                rows.append(
                    f"<div class='jp-grant-row {cls}'>"
                    f"<span class='jp-perm'>{sym}  {p['permission_key']}</span>"
                    f"<span class='jp-holder'>{p['holder_type']} <b>{p['holder_display']}</b> "
                    f"<span style='color:var(--jp-text-mute);'>⟨{p['holder_param']}⟩</span></span>"
                    f"</div>"
                )
            st.markdown("".join(rows), unsafe_allow_html=True)
            # Per-op remove
            rm_cols = st.columns(min(len(ops), 4) or 1)
            for i, p in enumerate(ops):
                if rm_cols[i % len(rm_cols)].button(
                    f"× drop op {i+1}",
                    key=f"drop_{sid}_{i}_{p['permission_key']}_{p['holder_param']}",
                ):
                    st.session_state["jp_pending"].remove(p)
                    st.rerun()

    st.markdown("---")
    # Confirmation gate
    confirm_text = f"APPLY {len(pending)}"
    typed = st.text_input(
        f"Type **{confirm_text}** to confirm and run all pending operations:",
        key="apply_confirm",
        disabled=not ADMIN,
    )
    cA, cB = st.columns([1, 1])
    apply_clicked = cA.button(
        f"🚀 Execute {len(pending)} operation(s)",
        type="primary",
        disabled=(not ADMIN) or (typed.strip() != confirm_text),
    )
    if cB.button("Discard pending queue"):
        _clear_pending()
        st.rerun()

    if apply_clicked:
        with st.spinner(f"Applying {len(pending)} operation(s) to Jira…"):
            ok, fail = _execute_pending()
        if fail == 0:
            st.success(f"All {ok} operation(s) applied successfully.")
        else:
            st.warning(f"{ok} succeeded, {fail} failed. Check the audit log tab for the failure detail.")
        # Don't auto-rerun — leave the success/warning visible. User can click
        # any tab to refresh.

else:
    st.markdown(
        "<div style='margin-top:1.5rem;text-align:center;color:var(--jp-text-mute);font-size:.85rem;'>"
        "No pending changes — queue grants or revokes from the tabs above to preview them here."
        "</div>",
        unsafe_allow_html=True,
    )
