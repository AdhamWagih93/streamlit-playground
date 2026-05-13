"""
Jira Permission Schemes — RBAC Access Lens

A single-page console that turns the native Jira DC permission scheme UI
into a role-aware access management surface. The page is built around three
ideas:

  1. Look up access by *role* first. The org policy is grant-by-LDAP-group;
     a user's roles are resolved from utils.rbac (VALID_USERS / VALID_GROUPS)
     intersected with their utils.ldap group memberships. Every per-user view
     answers "what can this user do AND why".

  2. Surface anomalies inline. A direct user-grant counts as stray (policy
     says grant by group). Holders that don't exist in LDAP are stray.
     Each anomaly has a one-click fix-it button with a popover confirm.

  3. Everything is filterable from popovers wired into the stat strip —
     project / scheme / role / holder. No tab gymnastics; the same page
     responds to whatever filter combination you set.

Every Jira write hits `jira_perm_audit` so the change history survives the
session. Approval workflow / pending queue / draft preview are gone — every
action takes effect as soon as you click through its inline confirm popover.
"""

from __future__ import annotations

import os
import io
import csv
import json
import time
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable

import requests
from requests.auth import HTTPBasicAuth
import streamlit as st

# Pandas is used for the role × permission standardization matrix display.
# Falls back to a plain HTML table if it isn't available.
try:
    import pandas as pd
    _PANDAS = True
except ImportError:
    pd = None  # type: ignore
    _PANDAS = False

# --- Project-internal modules (present in prod, optional locally) ----------
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

# --- RBAC source-of-truth dictionaries -------------------------------------
# VALID_GROUPS maps LDAP group CN → list of role names.
# VALID_USERS  maps Jira/LDAP username → list of role names (direct override).
# Both come from utils.rbac in the deployment; locally we degrade gracefully.
try:
    from utils.rbac import VALID_GROUPS, VALID_USERS  # type: ignore
    _RBAC_AVAILABLE = True
except ImportError:
    VALID_GROUPS: dict[str, list[str]] = {}
    VALID_USERS: dict[str, list[str]] = {}
    _RBAC_AVAILABLE = False

# --- LDAP helpers (read-only; vault-backed bind) ---------------------------
# Importing utils.ldap eagerly resolves a Vault client at module load, so we
# guard it behind a try and fall back to stubs in local dev.
try:
    from utils.ldap import (  # type: ignore
        get_user_info as _ldap_get_user_info,
        get_team_members as _ldap_get_team_members,
        get_user_email as _ldap_get_user_email,
    )
    _LDAP_AVAILABLE = True
except Exception:
    _LDAP_AVAILABLE = False

    def _ldap_get_user_info(username):  # type: ignore
        return None

    def _ldap_get_team_members(team_name):  # type: ignore
        return []

    def _ldap_get_user_email(username, preferred_domain=None):  # type: ignore
        return ""


def _extract_cn(dn: str) -> str:
    """Pull the CN out of a DN string ('CN=DEVOPS,OU=…' → 'DEVOPS').
    Local copy so we don't depend on utils.ldap private helpers."""
    if not dn:
        return ""
    m = re.search(r"CN=([^,]+)", dn)
    return m.group(1) if m else dn


# --- Postgres driver (v3 preferred, v2 fallback) ---------------------------
try:
    import psycopg as _psycopg  # type: ignore
    _POSTGRES_AVAILABLE = True
except ImportError:
    try:
        import psycopg2 as _psycopg  # type: ignore
        _POSTGRES_AVAILABLE = True
    except ImportError:
        _psycopg = None  # type: ignore
        _POSTGRES_AVAILABLE = False


# ---------------------------------------------------------------------------
# JiraAPI — verbatim from the user's snippet (vault-backed basic auth), plus
# env-var fallback for local dev.
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
# Page config + CSS
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Jira Access Lens",
    layout="wide",
    initial_sidebar_state="collapsed",
)

CUSTOM_CSS = """
<style>
:root {
    --jp-surface:   #ffffff;
    --jp-surface2:  #f7f8fb;
    --jp-surface3:  #eef1f8;
    --jp-border:    #e3e6ee;
    --jp-border-hi: #c7cce0;
    --jp-text:      #1a1d2e;
    --jp-text-dim:  #4a5068;
    --jp-text-mute: #8890a4;
    --jp-accent:    #0052cc;
    --jp-accent-lt: #deebff;
    --jp-green:     #059669;
    --jp-green-lt:  #d1fae5;
    --jp-red:       #dc2626;
    --jp-red-lt:    #fee2e2;
    --jp-amber:     #d97706;
    --jp-amber-lt:  #fef3c7;
    --jp-purple:    #7c3aed;
    --jp-purple-lt: #ede9fe;
    --jp-teal:      #0d9488;
    --jp-teal-lt:   #ccfbf1;
    --jp-pink:      #be185d;
    --jp-pink-lt:   #fce7f3;
    --jp-mono:      'SF Mono','Cascadia Code','Fira Code','Consolas',monospace;
}

.block-container { padding-top: 1rem; padding-bottom: 3rem; max-width: 1550px; }
h1,h2,h3,h4 { color: var(--jp-text); letter-spacing: -.01em; }

/* Header strip */
.jp-header {
    display: flex; align-items: baseline; gap: .8rem;
    padding-bottom: .4rem; margin-bottom: 1rem;
    border-bottom: 1px solid var(--jp-border);
}
.jp-header h1 { margin: 0; font-size: 1.55rem; font-weight: 600; }
.jp-header .jp-chip {
    font-size: .76rem; padding: .15rem .55rem; border-radius: 4px;
    border: 1px solid var(--jp-border); background: var(--jp-surface2);
    color: var(--jp-text-dim); font-family: var(--jp-mono);
}
.jp-header .jp-chip.jp-actor { background: var(--jp-accent-lt); color: var(--jp-accent); border-color: #b3d4ff; }

/* Pills, used everywhere */
.jp-pill {
    display: inline-block; padding: .14rem .55rem; border-radius: 999px;
    font-size: .72rem; font-weight: 500; line-height: 1.3;
    background: var(--jp-surface2); color: var(--jp-text-dim);
    border: 1px solid var(--jp-border); margin: .05rem .2rem .05rem 0;
}
.jp-pill.jp-role     { background: var(--jp-accent-lt); color: var(--jp-accent); border-color: #b3d4ff; }
.jp-pill.jp-user     { background: var(--jp-purple-lt); color: var(--jp-purple); border-color: #ddd6fe; }
.jp-pill.jp-group    { background: var(--jp-teal-lt);   color: var(--jp-teal); border-color: #99f6e4; }
.jp-pill.jp-stray    { background: var(--jp-red-lt);    color: var(--jp-red); border-color: #fecaca; }
.jp-pill.jp-warn     { background: var(--jp-amber-lt);  color: var(--jp-amber); border-color: #fde68a; }
.jp-pill.jp-ok       { background: var(--jp-green-lt);  color: var(--jp-green); border-color: #a7f3d0; }
.jp-pill.jp-info     { background: var(--jp-accent-lt); color: var(--jp-accent); border-color: #b3d4ff; }
.jp-pill.jp-mono     { font-family: var(--jp-mono); font-size: .68rem; }

/* KPI cards — bright big numbers */
.jp-kpi {
    background: var(--jp-surface); border: 1px solid var(--jp-border);
    border-radius: 10px; padding: .8rem 1rem; height: 100%;
}
.jp-kpi .jp-kpi-label {
    font-size: .7rem; text-transform: uppercase; letter-spacing: .06em;
    color: var(--jp-text-mute);
}
.jp-kpi .jp-kpi-num {
    font-family: var(--jp-mono); font-size: 1.9rem; font-weight: 700;
    line-height: 1.1; color: var(--jp-text);
}
.jp-kpi .jp-kpi-sub  { font-size: .73rem; color: var(--jp-text-dim); }
.jp-kpi.jp-kpi-accent { border-left: 4px solid var(--jp-accent); }
.jp-kpi.jp-kpi-green  { border-left: 4px solid var(--jp-green); }
.jp-kpi.jp-kpi-amber  { border-left: 4px solid var(--jp-amber); }
.jp-kpi.jp-kpi-purple { border-left: 4px solid var(--jp-purple); }
.jp-kpi.jp-kpi-red    { border-left: 4px solid var(--jp-red); }
.jp-kpi.jp-kpi-teal   { border-left: 4px solid var(--jp-teal); }

/* Filter strip — sits above KPIs */
.jp-filterbar {
    background: var(--jp-surface2); border: 1px solid var(--jp-border);
    border-radius: 10px; padding: .6rem .8rem; margin-bottom: .8rem;
}
.jp-filter-chips {
    display: flex; flex-wrap: wrap; gap: .3rem; margin-top: .4rem;
    min-height: 1.4rem;
}

/* Big section headers */
.jp-section-head {
    margin: 1.2rem 0 .6rem 0;
    padding-bottom: .4rem; border-bottom: 1px solid var(--jp-border);
    display: flex; align-items: baseline; gap: .6rem;
}
.jp-section-head h3 { margin: 0; font-size: 1.15rem; font-weight: 600; }
.jp-section-head .jp-section-sub {
    font-size: .82rem; color: var(--jp-text-mute);
}

/* Compliance cards: a row per role */
.jp-role-card {
    background: var(--jp-surface); border: 1px solid var(--jp-border);
    border-radius: 10px; padding: .75rem 1rem; margin-bottom: .5rem;
    border-left: 4px solid var(--jp-accent);
}
.jp-role-card.jp-has-stray { border-left-color: var(--jp-red); background: linear-gradient(90deg, #fff7f7 0%, #fff 12%); }
.jp-role-card .jp-role-head {
    display: flex; align-items: baseline; gap: .5rem;
    justify-content: space-between;
}
.jp-role-card .jp-role-name {
    font-weight: 700; font-size: 1.05rem; color: var(--jp-text);
    font-family: var(--jp-mono);
}
.jp-role-card .jp-role-stats {
    color: var(--jp-text-mute); font-size: .8rem;
}

/* Holder lens — clean detail card */
.jp-holder-card {
    background: var(--jp-surface); border: 1px solid var(--jp-border);
    border-radius: 12px; padding: 1rem 1.2rem; margin-bottom: 1rem;
}
.jp-holder-card .jp-holder-head {
    display: flex; align-items: baseline; gap: .8rem;
    padding-bottom: .5rem; border-bottom: 1px solid var(--jp-border);
    margin-bottom: .8rem;
}
.jp-holder-card .jp-holder-name {
    font-size: 1.2rem; font-weight: 700; color: var(--jp-text);
}
.jp-holder-card .jp-holder-id {
    font-family: var(--jp-mono); font-size: .85rem; color: var(--jp-text-dim);
}

/* Access map rows — the "why" column is critical */
.jp-access-row {
    display: grid;
    grid-template-columns: 1.2fr 1.5fr 1.7fr .6fr;
    gap: .5rem; padding: .35rem .55rem; align-items: center;
    border-bottom: 1px dashed var(--jp-border);
    font-size: .85rem;
}
.jp-access-row.jp-stray-row { background: #fff7f7; }
.jp-access-row.jp-shadow-row { background: #fffbeb; }
.jp-access-row .jp-perm-cell {
    font-family: var(--jp-mono); font-size: .78rem; color: var(--jp-accent);
}
.jp-access-row .jp-scheme-cell { color: var(--jp-text-dim); }
.jp-access-row .jp-why-cell { color: var(--jp-text-dim); font-size: .8rem; }

/* Banner */
.jp-banner {
    border-radius: 10px; padding: .75rem 1rem; margin-bottom: .8rem;
    border-left: 4px solid var(--jp-amber); background: #fff8e6;
    color: var(--jp-text);
}
.jp-banner.jp-banner-red   { border-left-color: var(--jp-red);   background: #fff2f2; }
.jp-banner.jp-banner-ok    { border-left-color: var(--jp-green); background: #f0fdf4; }
.jp-banner.jp-banner-info  { border-left-color: var(--jp-accent); background: #f0f6ff; }
.jp-banner b { color: var(--jp-text); }

/* Empty state */
.jp-empty {
    text-align: center; padding: 2rem 1rem; color: var(--jp-text-mute);
    background: var(--jp-surface2); border: 1px dashed var(--jp-border);
    border-radius: 10px; margin: .5rem 0;
}

/* Audit row */
.jp-audit-row {
    display: grid; grid-template-columns: 140px 70px 70px 1fr 140px;
    gap: .5rem; padding: .3rem .5rem;
    font-size: .78rem; border-bottom: 1px dashed var(--jp-border);
    align-items: center;
}
.jp-audit-row .jp-ts { font-family: var(--jp-mono); color: var(--jp-text-mute); }
.jp-audit-row .jp-status-ok  { color: var(--jp-green); font-weight: 600; }
.jp-audit-row .jp-status-err { color: var(--jp-red); font-weight: 600; }

/* Inline action mini-button row */
.jp-actions { display: flex; gap: .3rem; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Identity + admin gate
# ---------------------------------------------------------------------------
def _whoami() -> str:
    for k in ("username", "user"):
        v = st.session_state.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    v = st.session_state.get("email")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return os.environ.get("USER") or os.environ.get("USERNAME") or "anonymous"


def _is_admin() -> bool:
    roles = st.session_state.get("user_roles") or {}
    if isinstance(roles, dict):
        return "admin" in {str(k).strip().lower() for k in roles.keys()}
    if isinstance(roles, (list, tuple, set)):
        return "admin" in {str(r).strip().lower() for r in roles}
    return False

_LOCAL_DEV_BYPASS = os.environ.get("JIRA_PERMS_DEV_BYPASS") == "1"
ADMIN = _is_admin() or _LOCAL_DEV_BYPASS
ACTOR = _whoami()


# ---------------------------------------------------------------------------
# Postgres — minimal: one audit table.
# ---------------------------------------------------------------------------
POSTGRES_VAULT_PATH = os.environ.get("JIRA_PERMS_PG_VAULT_PATH", "postgres").strip()
POSTGRES_CONNECT_TIMEOUT = 10


@st.cache_data(ttl=600, show_spinner=False)
def _postgres_creds() -> dict:
    if not VaultClient:
        if os.environ.get("PGHOST"):
            return {
                "host":     os.environ.get("PGHOST", "").strip(),
                "port":     os.environ.get("PGPORT", "5432").strip(),
                "database": os.environ.get("PGDATABASE", "").strip(),
                "username": os.environ.get("PGUSER", "").strip(),
                "password": os.environ.get("PGPASSWORD", "").strip(),
            }
        return {}
    try:
        vc = VaultClient()
        cfg = vc.read_all_nested_secrets(POSTGRES_VAULT_PATH) or {}
    except Exception as e:
        logger.error(f"vault read for postgres failed: {e}")
        return {}
    if not cfg:
        return {}
    return {
        "host":     (cfg.get("host") or "").strip(),
        "port":     str(cfg.get("port") or "5432").strip(),
        "database": (cfg.get("database") or "").strip(),
        "username": (cfg.get("username") or "").strip(),
        "password": (cfg.get("password") or "").strip(),
    }


def _pg_connect():
    if not _POSTGRES_AVAILABLE:
        return None, "psycopg / psycopg2 not installed"
    creds = _postgres_creds()
    if not creds or not creds.get("host"):
        return None, "postgres creds not resolved (check vault path 'postgres')"
    try:
        try:
            _port = int(creds["port"])
        except (ValueError, TypeError):
            _port = 5432
        conn = _psycopg.connect(
            host=creds["host"], port=_port,
            dbname=creds["database"], user=creds["username"],
            password=creds["password"],
            connect_timeout=POSTGRES_CONNECT_TIMEOUT,
        )
        try:
            conn.autocommit = True
        except Exception:
            pass
        return conn, ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# Minimal audit table. We keep the legacy column names so any prior data
# from the v2 schema reads back transparently. `approval_id` is kept
# nullable for back-compat but never written by this revision.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jira_perm_audit (
    id              BIGSERIAL PRIMARY KEY,
    approval_id     BIGINT,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor           TEXT        NOT NULL,
    action          TEXT        NOT NULL,
    scheme_id       INTEGER     NOT NULL,
    scheme_name     TEXT,
    permission_key  TEXT        NOT NULL,
    holder_type     TEXT        NOT NULL,
    holder_param    TEXT        NOT NULL,
    holder_display  TEXT,
    ok              BOOLEAN     NOT NULL,
    status_code     INTEGER,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS jira_perm_audit_ts_idx     ON jira_perm_audit (ts DESC);
CREATE INDEX IF NOT EXISTS jira_perm_audit_actor_idx  ON jira_perm_audit (actor);
CREATE INDEX IF NOT EXISTS jira_perm_audit_scheme_idx ON jira_perm_audit (scheme_id);
"""


def _bootstrap_schema() -> tuple[bool, str]:
    if st.session_state.get("_jp_schema_ok"):
        return True, ""
    conn, err = _pg_connect()
    if err:
        return False, err
    try:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
        st.session_state["_jp_schema_ok"] = True
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_audit_insert(rows: list[dict]) -> tuple[int, str]:
    if not rows:
        return 0, ""
    conn, err = _pg_connect()
    if err:
        return 0, err
    try:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """
                    INSERT INTO jira_perm_audit
                      (actor, action, scheme_id, scheme_name, permission_key,
                       holder_type, holder_param, holder_display, ok,
                       status_code, error)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        r.get("actor") or "",
                        r.get("action") or "",
                        int(r.get("scheme_id") or 0),
                        r.get("scheme_name") or "",
                        r.get("permission_key") or "",
                        r.get("holder_type") or "",
                        r.get("holder_param") or "",
                        r.get("holder_display") or "",
                        bool(r.get("ok")),
                        r.get("status_code"),
                        r.get("error"),
                    ),
                )
        return len(rows), ""
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


@st.cache_data(ttl=30, show_spinner=False)
def db_audit_query(*, limit: int = 200) -> tuple[list[dict], str]:
    conn, err = _pg_connect()
    if err:
        return [], err
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, ts, actor, action, scheme_id, scheme_name,
                       permission_key, holder_type, holder_param, holder_display,
                       ok, status_code, error
                FROM jira_perm_audit
                ORDER BY ts DESC LIMIT %s
                """,
                (int(limit),),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()], ""
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Jira API helpers
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _api() -> JiraAPI:
    return JiraAPI()


def _full(path: str) -> str:
    base = _api().base_url.rstrip("/")
    return f"{base}{path}" if path.startswith("/") else f"{base}/{path}"


def _jira_write(method: str, path: str, **kwargs) -> tuple[bool, dict, int | None]:
    """Write path: returns (ok, body, status). Never raises. We keep the
    server's error body intact so audit rows carry actionable failures."""
    api = _api()
    url = _full(path)
    try:
        r = requests.request(method, url, auth=api.auth, timeout=kwargs.pop("timeout", 30), **kwargs)
        try:
            body = r.json() if r.text else {}
        except ValueError:
            body = {"raw": r.text}
        return 200 <= r.status_code < 300, body, r.status_code
    except requests.exceptions.RequestException as e:
        return False, {"error": str(e)}, None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_schemes() -> list[dict]:
    res = _api().request("GET", _full("/rest/api/2/permissionscheme"))
    if isinstance(res, dict) and "error" in res:
        st.error(f"Failed to list permission schemes: {res['error']}")
        return []
    return list((res or {}).get("permissionSchemes") or [])


@st.cache_data(ttl=300, show_spinner=False)
def fetch_scheme_detail(scheme_id: int) -> dict:
    res = _api().request(
        "GET",
        _full(f"/rest/api/2/permissionscheme/{int(scheme_id)}"),
        params={"expand": "permissions,user,group,projectRole,field,all"},
    )
    if isinstance(res, dict) and "error" in res:
        return {}
    return res or {}


@st.cache_data(ttl=900, show_spinner=False)
def fetch_permission_catalog() -> list[dict]:
    res = _api().request("GET", _full("/rest/api/2/permissions"))
    if isinstance(res, dict) and "error" in res:
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


@st.cache_data(ttl=900, show_spinner=False)
def fetch_scheme_to_projects() -> dict[int, list[dict]]:
    """scheme_id → [{key, name}, …]. Walks /project/search once."""
    bindings: dict[int, list[dict]] = {}
    start = 0
    page = 50
    while True:
        res = _api().request(
            "GET", _full("/rest/api/2/project/search"),
            params={"startAt": start, "maxResults": page},
        )
        if isinstance(res, dict) and "error" in res:
            res2 = _api().request("GET", _full("/rest/api/2/project"))
            if isinstance(res2, dict) and "error" in res2:
                return bindings
            for p in (res2 if isinstance(res2, list) else []):
                ps = _api().request("GET", _full(f"/rest/api/2/project/{p['key']}/permissionscheme"))
                if isinstance(ps, dict) and ps.get("id") is not None:
                    bindings.setdefault(int(ps["id"]), []).append(
                        {"key": p["key"], "name": p.get("name") or p["key"]}
                    )
            return bindings
        values = res.get("values") or []
        if not values:
            break
        for p in values:
            ps = _api().request("GET", _full(f"/rest/api/2/project/{p['key']}/permissionscheme"))
            if isinstance(ps, dict) and ps.get("id") is not None:
                bindings.setdefault(int(ps["id"]), []).append(
                    {"key": p["key"], "name": p.get("name") or p["key"]}
                )
        if res.get("isLast") or len(values) < page:
            break
        start += page
    return bindings


@st.cache_data(ttl=120, show_spinner=False)
def search_users(query: str, max_results: int = 30) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    res = _api().request(
        "GET", _full("/rest/api/2/user/picker"),
        params={"query": q, "maxResults": max_results, "showAvatar": False},
    )
    if isinstance(res, dict) and "error" in res:
        return []
    users = (res or {}).get("users") or []
    return [{
        "name": u.get("name") or u.get("key") or "",
        "key":  u.get("key") or u.get("name") or "",
        "display": u.get("displayName") or u.get("name") or "",
        "email": u.get("emailAddress") or "",
    } for u in users]


@st.cache_data(ttl=120, show_spinner=False)
def search_groups(query: str, max_results: int = 30) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    res = _api().request(
        "GET", _full("/rest/api/2/groups/picker"),
        params={"query": q, "maxResults": max_results},
    )
    if isinstance(res, dict) and "error" in res:
        return []
    return [{"name": g.get("name", "")} for g in (res or {}).get("groups") or []]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_devops_projects() -> list[dict]:
    """Read the (company, project) mapping from the devops_projects
    Postgres table — same source cicd_dashboard.py uses. Returns plain
    dicts so Streamlit can cache them. Empty list on any error (the page
    still works; the Company filter just goes blank)."""
    conn, err = _pg_connect()
    if err:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT company, project FROM devops_projects")
            rows = cur.fetchall()
            out: list[dict] = []
            for r in rows:
                company = (str(r[0]) if r[0] is not None else "").strip()
                project = (str(r[1]) if r[1] is not None else "").strip()
                if project:
                    out.append({"company": company, "project": project})
            return out
    except Exception as e:
        logger.warning(f"devops_projects read failed: {e}")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _invalidate_jira_cache():
    """Run after any Jira write. Clears the read-path caches that depend
    on grant state, plus the audit query cache so the audit log reflects
    the just-written row on the next render. ``cache_resource`` indices
    (_build_index, _build_group_grants_index, _compute_standardization_cached)
    self-invalidate via their signature args once ``all_grants`` changes."""
    fetch_all_schemes.clear()
    fetch_scheme_detail.clear()
    fetch_scheme_to_projects.clear()
    try:
        db_audit_query.clear()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# RBAC / LDAP helpers
# ---------------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def all_roles() -> list[str]:
    """Every distinct role name that appears anywhere in rbac.py."""
    out: set[str] = set()
    for v in VALID_GROUPS.values():
        for r in (v or []):
            if r:
                out.add(str(r).strip())
    for v in VALID_USERS.values():
        for r in (v or []):
            if r:
                out.add(str(r).strip())
    return sorted(out)


@st.cache_data(ttl=900, show_spinner=False)
def groups_for_role(role: str) -> list[str]:
    return sorted([g for g, rs in VALID_GROUPS.items() if role in (rs or [])])


@st.cache_data(ttl=900, show_spinner=False)
def users_for_role(role: str) -> list[str]:
    return sorted([u for u, rs in VALID_USERS.items() if role in (rs or [])])


@st.cache_data(ttl=3600, show_spinner=False)
def ldap_user_info_safe(username: str) -> dict | None:
    """LDAP user-info with a guard around any failure. None on miss/error."""
    if not username or not _LDAP_AVAILABLE:
        return None
    try:
        return _ldap_get_user_info(username)
    except Exception as e:
        logger.warning(f"LDAP get_user_info({username}) failed: {e}")
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def ldap_team_members_safe(group_cn: str) -> list[str]:
    if not group_cn or not _LDAP_AVAILABLE:
        return []
    try:
        return list(_ldap_get_team_members(group_cn) or [])
    except Exception as e:
        logger.warning(f"LDAP get_team_members({group_cn}) failed: {e}")
        return []


def roles_for_user(username: str) -> tuple[list[str], list[str]]:
    """Return (roles, sources). `sources` is a short trace of how the
    roles were derived — used in the UI to show provenance."""
    roles: set[str] = set()
    sources: list[str] = []
    if username in VALID_USERS:
        for r in VALID_USERS[username] or []:
            roles.add(r)
        sources.append(f"VALID_USERS[{username}] → {VALID_USERS[username]}")
    info = ldap_user_info_safe(username)
    if info:
        for dn in (info.get("groups") or []):
            cn = _extract_cn(dn)
            if cn in VALID_GROUPS:
                for r in VALID_GROUPS[cn] or []:
                    roles.add(r)
                sources.append(f"LDAP group {cn} → {VALID_GROUPS[cn]}")
    return sorted(roles), sources


# ---------------------------------------------------------------------------
# Domain model + grant parser
# ---------------------------------------------------------------------------
@dataclass
class Grant:
    scheme_id: int
    scheme_name: str
    permission_id: int
    permission_key: str
    holder_type: str
    holder_param: str
    holder_display: str


def _parse_grants(scheme: dict) -> list[Grant]:
    out: list[Grant] = []
    sid = int(scheme.get("id") or 0)
    sname = str(scheme.get("name") or "")
    for p in scheme.get("permissions") or []:
        holder = p.get("holder") or {}
        htype = str(holder.get("type") or "")
        hparam = str(holder.get("parameter") or "")
        display = hparam or "—"
        if htype == "user" and isinstance(holder.get("user"), dict):
            display = holder["user"].get("displayName") or hparam
        elif htype == "group" and isinstance(holder.get("group"), dict):
            display = holder["group"].get("name") or hparam
        elif htype == "projectRole" and isinstance(holder.get("projectRole"), dict):
            display = holder["projectRole"].get("name") or hparam
        out.append(Grant(
            scheme_id=sid, scheme_name=sname,
            permission_id=int(p.get("id") or 0),
            permission_key=str(p.get("permission") or ""),
            holder_type=htype, holder_param=hparam,
            holder_display=str(display),
        ))
    return out


@st.cache_data(ttl=300, show_spinner=False)
def all_grants() -> list[dict]:
    """Every grant on the instance, returned as plain dicts so Streamlit's
    cache layer can hash it. Converted back to Grant objects on read."""
    out: list[dict] = []
    for s in fetch_all_schemes():
        det = fetch_scheme_detail(int(s["id"]))
        for g in _parse_grants(det):
            out.append(g.__dict__.copy())
    return out


def _grants_as_objs(grants_dicts: list[dict]) -> list[Grant]:
    return [Grant(**d) for d in grants_dicts]


# ---------------------------------------------------------------------------
# Inline actions: grant / revoke / batch revoke. Each writes its audit row
# on completion (success or failure).
# ---------------------------------------------------------------------------
def do_grant(*, scheme_id: int, scheme_name: str, permission_key: str,
             holder_type: str, holder_param: str, holder_display: str) -> tuple[bool, str]:
    ok, body, status = _jira_write(
        "POST",
        f"/rest/api/2/permissionscheme/{int(scheme_id)}/permission",
        json={"holder": {"type": holder_type, "parameter": holder_param},
              "permission": permission_key},
    )
    err_str = None
    if not ok:
        e = body.get("errorMessages") or body.get("errors") or body.get("error") or body.get("raw")
        err_str = (json.dumps(e, default=str) if not isinstance(e, str) else e)[:1000]
    db_audit_insert([{
        "actor": ACTOR, "action": "grant",
        "scheme_id": scheme_id, "scheme_name": scheme_name,
        "permission_key": permission_key,
        "holder_type": holder_type, "holder_param": holder_param,
        "holder_display": holder_display,
        "ok": ok, "status_code": status, "error": err_str,
    }])
    _invalidate_jira_cache()
    all_grants.clear()
    return ok, err_str or ""


def do_revoke(*, scheme_id: int, scheme_name: str, permission_id: int,
              permission_key: str, holder_type: str, holder_param: str,
              holder_display: str) -> tuple[bool, str]:
    ok, body, status = _jira_write(
        "DELETE",
        f"/rest/api/2/permissionscheme/{int(scheme_id)}/permission/{int(permission_id)}",
    )
    err_str = None
    if not ok:
        e = body.get("errorMessages") or body.get("errors") or body.get("error") or body.get("raw")
        err_str = (json.dumps(e, default=str) if not isinstance(e, str) else e)[:1000]
    db_audit_insert([{
        "actor": ACTOR, "action": "revoke",
        "scheme_id": scheme_id, "scheme_name": scheme_name,
        "permission_key": permission_key,
        "holder_type": holder_type, "holder_param": holder_param,
        "holder_display": holder_display,
        "ok": ok, "status_code": status, "error": err_str,
    }])
    _invalidate_jira_cache()
    all_grants.clear()
    return ok, err_str or ""


def do_batch_revoke(grants_to_revoke: list[Grant]) -> tuple[int, int]:
    """Bulk-revoke without staging. Returns (ok, fail)."""
    ok_n, fail_n = 0, 0
    audit_rows: list[dict] = []
    for g in grants_to_revoke:
        ok, body, status = _jira_write(
            "DELETE",
            f"/rest/api/2/permissionscheme/{int(g.scheme_id)}/permission/{int(g.permission_id)}",
        )
        err_str = None
        if not ok:
            e = body.get("errorMessages") or body.get("errors") or body.get("error") or body.get("raw")
            err_str = (json.dumps(e, default=str) if not isinstance(e, str) else e)[:1000]
            fail_n += 1
        else:
            ok_n += 1
        audit_rows.append({
            "actor": ACTOR, "action": "revoke",
            "scheme_id": g.scheme_id, "scheme_name": g.scheme_name,
            "permission_key": g.permission_key,
            "holder_type": g.holder_type, "holder_param": g.holder_param,
            "holder_display": g.holder_display,
            "ok": ok, "status_code": status, "error": err_str,
        })
    db_audit_insert(audit_rows)
    _invalidate_jira_cache()
    all_grants.clear()
    return ok_n, fail_n


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------
def _ss_init():
    st.session_state.setdefault("flt_companies", [])
    st.session_state.setdefault("flt_projects", [])
    st.session_state.setdefault("flt_schemes", [])
    st.session_state.setdefault("flt_roles", [])
    st.session_state.setdefault("flt_holder", None)
    st.session_state.setdefault("focus_holder", None)  # the holder currently in the lens

_ss_init()


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
try:
    _host = _api().base_url
except Exception as e:
    _host = "(no connection)"
    st.error(f"Jira API initialization failed: {e}")
    st.stop()

rbac_chip = "rbac ✓" if _RBAC_AVAILABLE else "rbac ⚠ stub"
ldap_chip = "ldap ✓" if _LDAP_AVAILABLE else "ldap ⚠ stub"

st.markdown(
    f"""
<div class="jp-header">
  <h1>🛡️ Jira Access Lens</h1>
  <span class="jp-chip">{_host}</span>
  <span class="jp-chip jp-actor">👤 {ACTOR}{' · admin' if ADMIN else ''}</span>
  <span class="jp-chip">{rbac_chip}</span>
  <span class="jp-chip">{ldap_chip}</span>
  <span style="margin-left:auto;font-size:.78rem;color:var(--jp-text-mute);">
    role-aware · inline confirm · audited
  </span>
</div>
""",
    unsafe_allow_html=True,
)

_schema_ok, _schema_err = _bootstrap_schema()
if not _schema_ok:
    st.warning(
        f"📦 Audit table unavailable — Jira reads work but writes won't be "
        f"recorded. ({_schema_err})"
    )

if not ADMIN:
    st.warning("🔒 Read-only — admin role required to grant/revoke.")


# ---------------------------------------------------------------------------
# Sidebar — minimal
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Connection")
    st.caption(f"Jira: `{_host}`")
    pg_creds = _postgres_creds()
    pg_label = f"`{pg_creds.get('host','—')}/{pg_creds.get('database','—')}`" if pg_creds else "_(none)_"
    st.caption(f"Postgres: {pg_label}")
    st.caption(f"RBAC: {'loaded' if _RBAC_AVAILABLE else 'stubbed'} "
               f"({len(VALID_GROUPS)} groups · {len(VALID_USERS)} users)")
    st.caption(f"LDAP:  {'loaded' if _LDAP_AVAILABLE else 'stubbed'}")
    if st.button("🔄 Refresh caches", use_container_width=True):
        for fn in (fetch_all_schemes, fetch_scheme_detail, fetch_permission_catalog,
                   fetch_scheme_to_projects, search_users, search_groups,
                   ldap_user_info_safe, ldap_team_members_safe,
                   all_grants, all_roles, groups_for_role, users_for_role):
            try:
                fn.clear()
            except Exception:
                pass
        st.success("Cleared.")
        st.rerun()


# ---------------------------------------------------------------------------
# Load core data
# ---------------------------------------------------------------------------
with st.spinner("Loading schemes & grants…"):
    schemes = fetch_all_schemes()
    if not schemes:
        st.markdown(
            '<div class="jp-empty">No permission schemes returned. '
            'Check Vault config / Jira reachability.</div>',
            unsafe_allow_html=True,
        )
        st.stop()
    schemes_by_id: dict[int, dict] = {int(s["id"]): s for s in schemes if s.get("id") is not None}
    perm_catalog = fetch_permission_catalog()
    perm_name_by_key = {p["key"]: p["name"] for p in perm_catalog}
    perm_desc_by_key = {p["key"]: p["description"] for p in perm_catalog}
    perm_keys_sorted = [p["key"] for p in perm_catalog]
    scheme_to_projects = fetch_scheme_to_projects()
    grants_all = _grants_as_objs(all_grants())


# ---------------------------------------------------------------------------
# Project → scheme reverse map (used when filtering by project)
# ---------------------------------------------------------------------------
project_to_scheme_id: dict[str, int] = {}
for sid, projs in scheme_to_projects.items():
    for p in projs:
        project_to_scheme_id[p["key"]] = sid

all_project_keys = sorted(project_to_scheme_id.keys())
all_scheme_names = [(int(s["id"]), s["name"]) for s in schemes]

# Company dimension — sourced from the devops_projects Postgres table
# (same table cicd_dashboard.py reads). Project keys not present in the
# table fall under "(unmapped)" so they remain selectable.
_devops_rows = fetch_devops_projects()
project_to_company: dict[str, str] = {}
for r in _devops_rows:
    pkey = (r.get("project") or "").strip()
    comp = (r.get("company") or "").strip()
    if pkey:
        project_to_company[pkey] = comp or "(unmapped)"
# Make sure every Jira-known project is keyed (even if missing from DB)
for pk in all_project_keys:
    project_to_company.setdefault(pk, "(unmapped)")
company_to_projects: dict[str, set[str]] = {}
for pk, comp in project_to_company.items():
    company_to_projects.setdefault(comp, set()).add(pk)
all_companies = sorted(c for c in company_to_projects.keys() if c)

# Total size of the Jira permission catalog — denominator for the
# "X of Y permissions covered" stat that appears throughout the page.
total_permission_count = len(perm_catalog) or 1


@st.cache_data(ttl=300, show_spinner=False)
def _scheme_coverage_map(_grants_dicts: list[dict], total: int) -> dict[int, dict]:
    """Per-scheme permission coverage. Returns scheme_id → {covered, total,
    pct, perm_keys}. ``perm_keys`` is the sorted list of distinct
    permission keys with at least one holder in the scheme."""
    bucket: dict[int, set[str]] = {}
    for d in _grants_dicts:
        bucket.setdefault(int(d["scheme_id"]), set()).add(d["permission_key"])
    out: dict[int, dict] = {}
    for sid, keys in bucket.items():
        out[sid] = {
            "covered": len(keys),
            "total":   total,
            "pct":     (len(keys) / total) if total else 0.0,
            "perm_keys": sorted(keys),
        }
    # Schemes with zero grants don't appear in `bucket` — give them a row
    for sid in schemes_by_id:
        out.setdefault(sid, {"covered": 0, "total": total, "pct": 0.0, "perm_keys": []})
    return out

coverage_by_scheme = _scheme_coverage_map(all_grants(), total_permission_count)


# ---------------------------------------------------------------------------
# Stray-access analysis — per holder + per grant
#
# All the lookup structures are cached via ``st.cache_resource`` so they
# survive widget interactions and only re-build when the underlying grant
# list changes. ``cache_resource`` (rather than ``cache_data``) is used on
# purpose: it doesn't pickle the return value, which means Grant dataclass
# instances pass through fine.
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _build_index(_grants_dicts_signature: str, _grants_dicts: list[dict]) -> dict:
    """Per-holder / per-scheme grant buckets. Signature arg drives the cache
    key — pass a fingerprint of the grant list (length is good enough; any
    write to Jira invalidates ``all_grants`` which changes the count)."""
    g_objs = _grants_as_objs(_grants_dicts)
    grants_by_user: dict[str, list[Grant]] = {}
    grants_by_group: dict[str, list[Grant]] = {}
    grants_by_scheme: dict[int, list[Grant]] = {}
    for g in g_objs:
        grants_by_scheme.setdefault(g.scheme_id, []).append(g)
        if g.holder_type == "user":
            grants_by_user.setdefault(g.holder_param, []).append(g)
        elif g.holder_type == "group":
            grants_by_group.setdefault(g.holder_param, []).append(g)
    return {
        "grants_by_user": grants_by_user,
        "grants_by_group": grants_by_group,
        "grants_by_scheme": grants_by_scheme,
        "user_holders": sorted(grants_by_user.keys()),
        "group_holders": sorted(grants_by_group.keys()),
    }


@st.cache_resource(show_spinner=False)
def _build_group_grants_index(_grants_dicts_signature: str, _grants_dicts: list[dict]) -> dict:
    """(scheme_id, permission_key) → list of group Grants. Used by the
    stray detector so per-user work is O(direct_grants) instead of
    O(all_grants × direct_grants)."""
    idx: dict[tuple[int, str], list[Grant]] = {}
    for d in _grants_dicts:
        if d["holder_type"] == "group":
            idx.setdefault((int(d["scheme_id"]), d["permission_key"]), []).append(Grant(**d))
    return idx


def _grants_signature(grants_dicts: list[dict]) -> str:
    """Cheap fingerprint of the grant list — len is enough since any write
    bumps it and Streamlit's underlying caches refresh together."""
    return f"n={len(grants_dicts)}"


_g_sig = _grants_signature(all_grants())
index = _build_index(_g_sig, all_grants())
_GROUP_GRANTS_INDEX = _build_group_grants_index(_g_sig, all_grants())


def membership_of_group(group_name: str) -> set[str]:
    return set(ldap_team_members_safe(group_name))


def user_membership_groups(username: str) -> set[str]:
    info = ldap_user_info_safe(username)
    if not info:
        return set()
    return {_extract_cn(dn) for dn in (info.get("groups") or [])}


def detect_stray_for_user(username: str, grants: list[Grant]) -> list[dict]:
    """Per direct user grant: is it covered by a group the user is a
    member of? Uses the precomputed group-grants index — no inner loop
    over all_grants."""
    user_groups = user_membership_groups(username)
    direct_grants = [g for g in grants if g.holder_type == "user"]
    flags: list[dict] = []
    for dg in direct_grants:
        candidates = _GROUP_GRANTS_INDEX.get((dg.scheme_id, dg.permission_key), [])
        covered = [c.holder_param for c in candidates if c.holder_param in user_groups]
        flags.append({
            "grant": dg,
            "covered_by_groups": covered,
            "severity": "shadow" if covered else "exclusive",
        })
    return flags


def explain_group_grant(group_name: str, username: str) -> str:
    members = membership_of_group(group_name)
    if not members:
        return "group has no LDAP members or LDAP lookup failed"
    if username in members:
        return f"user is a member of {group_name} in LDAP"
    return f"user is NOT in {group_name} in LDAP — this grant doesn't actually apply"


# ---------------------------------------------------------------------------
# Filter resolution — what does the current filter set narrow down to?
# ---------------------------------------------------------------------------
def resolve_filter() -> dict:
    """Translate the active filter selections into a single canonical
    result — projects_in_view, schemes_in_view, users_in_view,
    groups_in_view, and a label set for the chip strip."""
    flt_companies: list[str] = list(st.session_state["flt_companies"] or [])
    flt_projects: list[str] = list(st.session_state["flt_projects"] or [])
    flt_schemes: list[int] = list(st.session_state["flt_schemes"] or [])
    flt_roles: list[str] = list(st.session_state["flt_roles"] or [])
    flt_holder: dict | None = st.session_state["flt_holder"]

    # Projects-in-view: start from all known projects, narrow by company,
    # then by explicit project picks.
    projects_set: set[str] = set(all_project_keys)
    if flt_companies:
        projects_set = set()
        for c in flt_companies:
            projects_set |= company_to_projects.get(c, set())
        projects_set &= set(all_project_keys)
    if flt_projects:
        projects_set &= set(flt_projects)

    # Schemes-in-view: start from the schemes bound to those projects, then
    # narrow further by the explicit scheme picks. If no project or company
    # filter is active, all schemes are eligible (incl. those bound to no
    # project — they wouldn't be reachable via the project path).
    if flt_companies or flt_projects:
        schemes_set: set[int] = {
            project_to_scheme_id[p] for p in projects_set
            if p in project_to_scheme_id
        }
    else:
        schemes_set = set(schemes_by_id.keys())
    if flt_schemes:
        schemes_set &= set(flt_schemes)

    users_by_role: set[str] = set()
    groups_by_role: set[str] = set()
    for role in flt_roles:
        users_by_role.update(users_for_role(role))
        groups_by_role.update(groups_for_role(role))

    pinned_user = None
    pinned_group = None
    if flt_holder:
        if flt_holder["type"] == "user":
            pinned_user = flt_holder["param"]
        elif flt_holder["type"] == "group":
            pinned_group = flt_holder["param"]

    chips: list[str] = []
    if flt_companies:
        chips.append(f"<span class='jp-pill jp-info'>🏢 {', '.join(flt_companies)}</span>")
    if flt_projects:
        chips.append(f"<span class='jp-pill jp-info'>📁 {', '.join(flt_projects[:5])}{'…' if len(flt_projects)>5 else ''}</span>")
    if flt_schemes:
        chips.append(f"<span class='jp-pill jp-info'>📋 {len(flt_schemes)} scheme(s)</span>")
    if flt_roles:
        chips.append(f"<span class='jp-pill jp-role'>🎭 {', '.join(flt_roles)}</span>")
    if flt_holder:
        icon = "👤" if flt_holder["type"] == "user" else "👥"
        chips.append(f"<span class='jp-pill jp-{flt_holder['type']}'>{icon} {flt_holder['display']}</span>")

    return {
        "companies_in_view": set(flt_companies) if flt_companies else set(all_companies),
        "projects_in_view": projects_set,
        "schemes_in_view": schemes_set,
        "users_by_role": users_by_role,
        "groups_by_role": groups_by_role,
        "pinned_user": pinned_user,
        "pinned_group": pinned_group,
        "chips_html": " ".join(chips),
    }


def grants_in_view(filt: dict) -> list[Grant]:
    out = []
    for g in grants_all:
        if g.scheme_id not in filt["schemes_in_view"]:
            continue
        if filt["pinned_user"]:
            # Show grants where this user is involved — direct OR via a group they belong to
            if g.holder_type == "user" and g.holder_param == filt["pinned_user"]:
                pass
            elif g.holder_type == "group" and filt["pinned_user"] in membership_of_group(g.holder_param):
                pass
            else:
                continue
        if filt["pinned_group"]:
            if not (g.holder_type == "group" and g.holder_param == filt["pinned_group"]):
                continue
        if filt["users_by_role"] or filt["groups_by_role"]:
            # Role-scoped: keep direct-user grants for role-users, AND group
            # grants whose group has the role.
            if g.holder_type == "user" and g.holder_param in filt["users_by_role"]:
                pass
            elif g.holder_type == "group" and g.holder_param in filt["groups_by_role"]:
                pass
            else:
                continue
        out.append(g)
    return out


# ---------------------------------------------------------------------------
# Filter bar — inline searchable multiselects (companies / projects / schemes
# / roles) for type-as-you-go filtering, plus popovers for holder pin and
# the quick-grant action.
# ---------------------------------------------------------------------------
st.markdown("<div class='jp-filterbar'>", unsafe_allow_html=True)

# Row 1 — searchable dropdowns. Streamlit's multiselect filters its
# options as you type, so even a 200-entry list stays usable.
flt_r1 = st.columns([1.1, 1.4, 1.4, 1.2])

with flt_r1[0]:
    new_cs = st.multiselect(
        "🏢 Companies",
        options=all_companies,
        default=st.session_state["flt_companies"],
        placeholder="any company" if all_companies else "(devops_projects empty / unreachable)",
        key="flt_companies_ms",
        help="Sourced from the devops_projects Postgres table. "
             "Filters projects (and therefore schemes) to those owned by the company.",
    )
    if new_cs != st.session_state["flt_companies"]:
        st.session_state["flt_companies"] = new_cs
        st.rerun()

with flt_r1[1]:
    # If a company filter is active, narrow the project pick-list to that
    # company's projects so the dropdown shrinks. Empty company filter →
    # everything is selectable.
    if st.session_state["flt_companies"]:
        proj_opts = sorted({
            p for c in st.session_state["flt_companies"]
            for p in company_to_projects.get(c, set())
            if p in project_to_scheme_id  # only show projects we know schemes for
        })
    else:
        proj_opts = all_project_keys
    new_ps = st.multiselect(
        "📁 Projects",
        options=proj_opts,
        default=[p for p in st.session_state["flt_projects"] if p in proj_opts],
        placeholder="any project",
        format_func=lambda k: f"{k}  · {project_to_company.get(k, '(unmapped)')}",
        key="flt_projects_ms",
    )
    if new_ps != st.session_state["flt_projects"]:
        st.session_state["flt_projects"] = new_ps
        st.rerun()

with flt_r1[2]:
    scheme_options = {sid: name for sid, name in all_scheme_names}
    new_sc = st.multiselect(
        "📋 Schemes",
        options=list(scheme_options.keys()),
        default=st.session_state["flt_schemes"],
        format_func=lambda sid: (
            f"{scheme_options[sid]}  · "
            f"{coverage_by_scheme.get(sid, {}).get('covered', 0)}/"
            f"{total_permission_count} perms"
        ),
        placeholder="any scheme",
        key="flt_schemes_ms",
    )
    if new_sc != st.session_state["flt_schemes"]:
        st.session_state["flt_schemes"] = new_sc
        st.rerun()

with flt_r1[3]:
    roles_known = all_roles()
    new_rs = st.multiselect(
        "🎭 Roles",
        options=roles_known,
        default=st.session_state["flt_roles"],
        placeholder="any role" if roles_known else "(no roles in utils.rbac)",
        key="flt_roles_ms",
        help="RBAC roles from utils.rbac.VALID_USERS / VALID_GROUPS.",
    )
    if new_rs != st.session_state["flt_roles"]:
        st.session_state["flt_roles"] = new_rs
        st.rerun()

# Row 2 — action popovers and reset
flt_r2 = st.columns([1.4, 1.4, .8, 1.2])

with flt_r2[0]:
    with st.popover("👤 Pin holder", use_container_width=True):
        st.caption("Scope the whole page to a single user or group.")
        ht = st.radio("Type", ["user", "group"], horizontal=True, key="flt_holder_type_pop")
        q = st.text_input("Search…", key="flt_holder_q_pop", placeholder="≥ 2 chars")
        if q and len(q.strip()) >= 2:
            results = search_users(q) if ht == "user" else search_groups(q)
            opts = (
                {f"{r['display']}  ⟨{r['name']}⟩": r for r in results}
                if ht == "user"
                else {r["name"]: r for r in results}
            )
            if opts:
                pick = st.selectbox("Select", list(opts.keys()), key="flt_holder_pick_pop")
                chosen = opts[pick]
                if st.button("Pin holder", key="flt_holder_pin_btn"):
                    if ht == "user":
                        st.session_state["flt_holder"] = {
                            "type": "user", "param": chosen["name"],
                            "display": chosen["display"] or chosen["name"],
                        }
                    else:
                        st.session_state["flt_holder"] = {
                            "type": "group", "param": chosen["name"],
                            "display": chosen["name"],
                        }
                    st.rerun()
            else:
                st.caption(f"No matching {ht}s.")
        if st.session_state["flt_holder"]:
            st.caption(f"Currently pinned: **{st.session_state['flt_holder']['display']}**")
            if st.button("Unpin", key="flt_holder_unpin_btn"):
                st.session_state["flt_holder"] = None
                st.rerun()

with flt_r2[1]:
    with st.popover("➕ Quick grant", use_container_width=True, disabled=not ADMIN):
        st.markdown("**Grant a permission to a holder**")
        qg_type = st.radio("Holder type", ["user", "group"], horizontal=True, key="qg_type")
        qg_q = st.text_input("Search holder", key="qg_q")
        qg_holder = None
        if qg_q and len(qg_q.strip()) >= 2:
            results = search_users(qg_q) if qg_type == "user" else search_groups(qg_q)
            opts = (
                {f"{r['display']}  ⟨{r['name']}⟩": r for r in results}
                if qg_type == "user"
                else {r["name"]: r for r in results}
            )
            if opts:
                pick = st.selectbox("Holder", list(opts.keys()), key="qg_pick")
                qg_holder = opts[pick]
        qg_scheme = st.selectbox(
            "Scheme", list(schemes_by_id.keys()),
            format_func=lambda sid: (
                f"{schemes_by_id[sid]['name']}  · "
                f"{coverage_by_scheme.get(sid, {}).get('covered', 0)}/"
                f"{total_permission_count} perms"
            ),
            key="qg_scheme",
        )
        qg_perm = st.selectbox(
            "Permission", perm_keys_sorted,
            format_func=lambda k: f"{perm_name_by_key.get(k, k)}  ⟨{k}⟩",
            key="qg_perm",
        )
        if qg_holder:
            display = (qg_holder.get("display") or qg_holder.get("name") or "")
            param = qg_holder["name"]
            st.markdown(
                f"<div class='jp-banner jp-banner-info'>"
                f"<b>Confirm:</b> grant <span class='jp-pill jp-mono'>{qg_perm}</span> "
                f"to <b>{qg_type}</b> <b>{display}</b> ⟨{param}⟩ "
                f"on scheme <b>{schemes_by_id[qg_scheme]['name']}</b>."
                f"</div>",
                unsafe_allow_html=True,
            )
            if st.button("✅ Apply grant", type="primary", key="qg_apply", disabled=not ADMIN):
                ok, err = do_grant(
                    scheme_id=qg_scheme,
                    scheme_name=schemes_by_id[qg_scheme]["name"],
                    permission_key=qg_perm,
                    holder_type=qg_type, holder_param=param,
                    holder_display=display,
                )
                if ok:
                    st.success("Granted.")
                    st.rerun()
                else:
                    st.error(err or "Grant failed.")

with flt_r2[2]:
    if st.button("❎ Reset", use_container_width=True):
        st.session_state["flt_companies"] = []
        st.session_state["flt_projects"] = []
        st.session_state["flt_schemes"] = []
        st.session_state["flt_roles"] = []
        st.session_state["flt_holder"] = None
        st.rerun()

with flt_r2[3]:
    st.caption(
        f"{len(all_companies)} compan{'y' if len(all_companies)==1 else 'ies'} · "
        f"{len(all_project_keys)} projects · {len(schemes_by_id)} schemes"
    )

st.markdown("</div>", unsafe_allow_html=True)

# Active filter chip line
filt = resolve_filter()
if filt["chips_html"]:
    st.markdown(
        f"<div style='margin-top:-.4rem;margin-bottom:.7rem;'>{filt['chips_html']}</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# KPI strip — responds to the filter set
# ---------------------------------------------------------------------------
view_grants = grants_in_view(filt)
v_users = sorted({g.holder_param for g in view_grants if g.holder_type == "user"})
v_groups = sorted({g.holder_param for g in view_grants if g.holder_type == "group"})
v_schemes = sorted({g.scheme_id for g in view_grants})
v_projects: set[str] = set()
v_companies: set[str] = set()
for sid in v_schemes:
    for p in scheme_to_projects.get(sid, []):
        v_projects.add(p["key"])
        v_companies.add(project_to_company.get(p["key"], "(unmapped)"))

# Permission coverage in current view — distinct permission keys with any
# grant in the filter set, denominator = full Jira catalog size.
v_perm_keys = sorted({g.permission_key for g in view_grants})
v_perm_pct = (len(v_perm_keys) / total_permission_count * 100) if total_permission_count else 0

# Stray = direct user grants in the view
stray_grants = [g for g in view_grants if g.holder_type == "user"]

kc1, kc2, kc3, kc4, kc5, kc6, kc7 = st.columns(7)
def _kpi(col, label, num, sub, klass):
    col.markdown(
        f"<div class='jp-kpi {klass}'>"
        f"<div class='jp-kpi-label'>{label}</div>"
        f"<div class='jp-kpi-num'>{num}</div>"
        f"<div class='jp-kpi-sub'>{sub}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

_kpi(kc1, "Companies",   f"{len(v_companies):,}",
     f"of {len(all_companies)} total",                              "jp-kpi-teal")
_kpi(kc2, "Projects",    f"{len(v_projects):,}",
     f"of {len(all_project_keys)} bound to schemes",                "jp-kpi-amber")
_kpi(kc3, "Schemes",     f"{len(v_schemes):,}",
     f"of {len(schemes_by_id)} on instance",                        "jp-kpi-accent")
_kpi(kc4, "Permissions", f"{len(v_perm_keys)}/{total_permission_count}",
     f"{v_perm_pct:.0f}% of catalog covered",                       "jp-kpi-purple")
_kpi(kc5, "Users",       f"{len(v_users):,}",
     "distinct direct user holders",                                "jp-kpi-purple")
_kpi(kc6, "Groups",      f"{len(v_groups):,}",
     "distinct group holders",                                      "jp-kpi-teal")
_kpi(kc7, "Stray",       f"{len(stray_grants):,}",
     "direct grants (policy: by-group)",
     "jp-kpi-red" if stray_grants else "jp-kpi-green")


# ---------------------------------------------------------------------------
# Stray summary banner — opens an expander listing all of them with batch fix
# ---------------------------------------------------------------------------
@st.fragment
def _section_stray():
    if not stray_grants:
        return
    with st.expander(
        f"⚠️  {len(stray_grants)} stray access pattern(s) — direct user grants in view",
        expanded=False,
    ):
        st.caption(
            "Each row below is a *direct* user grant. Org policy is to grant "
            "access via LDAP groups, so direct grants are exceptions that "
            "should be either justified or revoked. The right-hand chip tells "
            "you whether the user already gets the same access via a group "
            "(safe-to-revoke) or whether removing this grant would actually "
            "strip their access (exclusive)."
        )

        # Group strays by user so the user can fix one person at a time
        by_user: dict[str, list[Grant]] = {}
        for g in stray_grants:
            by_user.setdefault(g.holder_param, []).append(g)

        for uname, ulist in sorted(by_user.items()):
            flags = detect_stray_for_user(uname, ulist)
            n_shadow = sum(1 for f in flags if f["severity"] == "shadow")
            n_exclusive = sum(1 for f in flags if f["severity"] == "exclusive")
            ucols = st.columns([3, 1, 1, 1])
            ucols[0].markdown(
                f"**👤 {ulist[0].holder_display}** ⟨`{uname}`⟩ — {len(ulist)} direct grant(s)"
            )
            if n_shadow:
                ucols[1].markdown(f"<span class='jp-pill jp-warn'>{n_shadow} shadow</span>", unsafe_allow_html=True)
            if n_exclusive:
                ucols[2].markdown(f"<span class='jp-pill jp-stray'>{n_exclusive} exclusive</span>", unsafe_allow_html=True)
            with ucols[3].popover("Lens →"):
                st.markdown("Open this user's full access lens below.")
                if st.button("Focus user", key=f"strayfocus_{uname}"):
                    st.session_state["focus_holder"] = {
                        "type": "user", "param": uname,
                        "display": ulist[0].holder_display,
                    }
                    st.rerun()

            # Per-row controls
            for f in flags:
                g = f["grant"]
                badge = (
                    f"<span class='jp-pill jp-warn'>shadow · covered by {', '.join(f['covered_by_groups'][:2])}</span>"
                    if f["severity"] == "shadow"
                    else "<span class='jp-pill jp-stray'>exclusive · revoke removes access</span>"
                )
                rcols = st.columns([3, 3, 1])
                rcols[0].markdown(
                    f"<div class='jp-access-row jp-stray-row'>"
                    f"<span class='jp-perm-cell'>{g.permission_key}</span>"
                    f"<span class='jp-scheme-cell'>{g.scheme_name}</span>"
                    f"<span class='jp-why-cell'>{badge}</span>"
                    f"<span></span></div>",
                    unsafe_allow_html=True,
                )
                with rcols[2].popover("⊖", use_container_width=True, disabled=not ADMIN):
                    st.markdown(
                        f"**Confirm:** revoke `{g.permission_key}` from user "
                        f"`{uname}` on scheme **{g.scheme_name}**."
                    )
                    if f["severity"] == "shadow":
                        st.caption(f"User is in {', '.join(f['covered_by_groups'])} — access is preserved via that group.")
                    else:
                        st.caption("⚠️ User has no group that grants this permission. Revoking removes the access.")
                    if st.button("Apply revoke", key=f"stray_rv_{g.scheme_id}_{g.permission_id}", type="primary", disabled=not ADMIN):
                        ok, err = do_revoke(
                            scheme_id=g.scheme_id, scheme_name=g.scheme_name,
                            permission_id=g.permission_id, permission_key=g.permission_key,
                            holder_type=g.holder_type, holder_param=g.holder_param,
                            holder_display=g.holder_display,
                        )
                        if ok:
                            st.success("Revoked.")
                            st.rerun()
                        else:
                            st.error(err or "Revoke failed.")

            # Per-user batch action: revoke all shadows
            shadows_only = [f["grant"] for f in flags if f["severity"] == "shadow"]
            if shadows_only and ADMIN:
                with st.popover(f"Revoke all {len(shadows_only)} shadow grant(s)", disabled=not ADMIN):
                    st.markdown(
                        f"**Confirm batch revoke:** drop {len(shadows_only)} "
                        f"redundant direct grant(s) for `{uname}`. Each is "
                        f"already covered by a group the user belongs to."
                    )
                    if st.button("Apply batch revoke", key=f"stray_batch_{uname}", type="primary"):
                        ok_n, fail_n = do_batch_revoke(shadows_only)
                        if fail_n == 0:
                            st.success(f"Revoked {ok_n} grant(s).")
                        else:
                            st.warning(f"{ok_n} ok, {fail_n} failed.")
                        st.rerun()
            st.divider()


_section_stray()


# ---------------------------------------------------------------------------
# Section: Compliance by role — one card per RBAC role
# Wrapped as a fragment so expander clicks and per-role buttons don't
# trigger a full-page rerun (they only re-render this section).
# ---------------------------------------------------------------------------
@st.fragment
def _section_compliance():
    st.markdown(
        "<div class='jp-section-head'><h3>🎭 Compliance by role</h3>"
        "<span class='jp-section-sub'>From utils.rbac · click a card to inspect "
        "members and their Jira access</span></div>",
        unsafe_allow_html=True,
    )

    roles_in_view = st.session_state["flt_roles"] or all_roles()
    if not roles_in_view:
        st.markdown(
            "<div class='jp-empty'>No roles defined in utils.rbac — the page "
            "still works for raw Jira access management, but role-based "
            "compliance views are empty until VALID_GROUPS / VALID_USERS are populated.</div>",
            unsafe_allow_html=True,
        )
    else:
        role_cols = st.columns(2)
        for i, role in enumerate(roles_in_view):
            col = role_cols[i % 2]
            with col:
                users = users_for_role(role)
                groups = groups_for_role(role)
                # Aggregate grants for the role: direct-user grants + group grants
                role_user_grants = [g for g in view_grants if g.holder_type == "user" and g.holder_param in users]
                role_group_grants = [g for g in view_grants if g.holder_type == "group" and g.holder_param in groups]
                stray_for_role = role_user_grants  # by policy
                has_stray = bool(stray_for_role)
    
                klass = "jp-has-stray" if has_stray else ""
                stray_chip = (
                    f"<span class='jp-pill jp-stray'>{len(stray_for_role)} stray</span>"
                    if has_stray else "<span class='jp-pill jp-ok'>clean</span>"
                )
                col.markdown(
                    f"<div class='jp-role-card {klass}'>"
                    f"<div class='jp-role-head'>"
                    f"<div><span class='jp-role-name'>{role}</span></div>"
                    f"<div class='jp-role-stats'>"
                    f"<span class='jp-pill jp-user'>{len(users)} users</span>"
                    f"<span class='jp-pill jp-group'>{len(groups)} groups</span>"
                    f"{stray_chip}"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )
                with col.expander(f"Inspect role: {role}", expanded=False):
                    ec1, ec2 = st.columns(2)
                    with ec1:
                        st.markdown("**Users in this role** (via VALID_USERS)")
                        if not users:
                            st.caption("_none_")
                        # No LDAP enrichment here — would do N round-trips per
                        # role and gum up the page. The holder lens fetches it
                        # on demand when the user clicks through.
                        for u in users:
                            if st.button(
                                f"👤 {u}",
                                key=f"rolelens_u_{role}_{u}",
                                use_container_width=True,
                            ):
                                st.session_state["focus_holder"] = {
                                    "type": "user", "param": u, "display": u,
                                }
                                st.rerun()
                    with ec2:
                        st.markdown("**LDAP groups → this role** (via VALID_GROUPS)")
                        if not groups:
                            st.caption("_none_")
                        # Same idea — don't fetch members eagerly. The lens
                        # surfaces the membership when you click in.
                        for g in groups:
                            if st.button(
                                f"👥 {g}",
                                key=f"rolelens_g_{role}_{g}",
                                use_container_width=True,
                            ):
                                st.session_state["focus_holder"] = {
                                    "type": "group", "param": g, "display": g,
                                }
                                st.rerun()
                    if stray_for_role:
                        st.markdown(
                            f"**⚠️ Stray direct grants for this role:** {len(stray_for_role)}"
                        )
                        for g in stray_for_role[:12]:
                            st.markdown(
                                f"- `{g.permission_key}` on **{g.scheme_name}** "
                                f"to `{g.holder_param}`"
                            )
                        if len(stray_for_role) > 12:
                            st.caption(f"…and {len(stray_for_role) - 12} more (use the stray banner above for full list).")


_section_compliance()


# ---------------------------------------------------------------------------
# Section: Role × permission standardization
# Helpers stay at module level so their @st.cache_resource decoration is
# stable across reruns. The rendering itself is wrapped as a fragment
# further down — _section_standardization() — so matrix-local widgets
# don't trigger a full-page rerun.
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def _compute_standardization_cached(
    _sig: str,
    roles_tuple: tuple[str, ...],
    perms_tuple: tuple[str, ...],
    view_grants_tuple: tuple,        # tuple of (holder_type, holder_param, scheme_id, permission_key)
) -> tuple[list[dict], dict[str, set[int]], dict[tuple[str, str], set[int]]]:
    """Pure compute: role × permission coverage across role-relevant
    schemes. Cached via cache_resource (return contains sets, so it isn't
    pickle-friendly via cache_data). The signature arg is a coarse
    fingerprint of the filter + grant set that keys the cache."""
    role_holders: dict[str, set[tuple[str, str]]] = {}
    for role in roles_tuple:
        ru = set(users_for_role(role))
        rg = set(groups_for_role(role))
        role_holders[role] = {("user", u) for u in ru} | {("group", g) for g in rg}

    role_relevant: dict[str, set[int]] = {r: set() for r in roles_tuple}
    rp_granted: dict[tuple[str, str], set[int]] = {}

    for htype, hparam, sid, pkey in view_grants_tuple:
        key = (htype, hparam)
        for role, holders in role_holders.items():
            if key in holders:
                role_relevant[role].add(sid)
                rp_granted.setdefault((role, pkey), set()).add(sid)

    rows: list[dict] = []
    for role in roles_tuple:
        rel = role_relevant.get(role, set())
        if not rel:
            continue
        for perm in perms_tuple:
            granted = rp_granted.get((role, perm), set())
            n = len(granted)
            total = len(rel)
            pct = (n / total * 100) if total else 0
            if n == 0 and total > 0:
                status = "⛔ never"
            elif n == total:
                status = "✅ all"
            else:
                status = "⚠️ partial"
            rows.append({
                "Role": role,
                "Permission key": perm,
                "Permission name": perm_name_by_key.get(perm, perm),
                "Granted in": n,
                "Out of": total,
                "Coverage %": round(pct, 1),
                "Status": status,
            })
    return rows, role_relevant, rp_granted


def _compute_standardization(
    roles_list: list[str],
    perms_list: list[str],
    view_grants_list: list[Grant],
) -> tuple[list[dict], dict[str, set[int]], dict[tuple[str, str], set[int]]]:
    """Thin wrapper that hashes inputs into a stable cache key and calls
    the cached version with primitive tuples."""
    view_tuple = tuple(
        (g.holder_type, g.holder_param, g.scheme_id, g.permission_key)
        for g in view_grants_list
    )
    sig = f"r{len(roles_list)}_p{len(perms_list)}_v{len(view_tuple)}"
    return _compute_standardization_cached(
        sig, tuple(roles_list), tuple(perms_list), view_tuple,
    )


@st.fragment
def _section_standardization():
    st.markdown(
        "<div class='jp-section-head'><h3>🧭 Role × permission standardization</h3>"
        "<span class='jp-section-sub'>For each role, which permissions are granted "
        "consistently across the schemes that role touches — and which deviate.</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    # Permission universe: only permissions that are actually granted to
    # anyone anywhere in the filter set — keeps the matrix dense.
    _perm_universe_in_view = sorted({g.permission_key for g in view_grants})

    # Role universe: filter selection if set, else every role with at least
    # one relevant scheme.
    _roles_for_matrix = st.session_state["flt_roles"] or all_roles()

    if not _roles_for_matrix or not _perm_universe_in_view:
        st.markdown(
            "<div class='jp-empty'>Need at least one role (from utils.rbac) and "
            "one permission with grants in the current filter set to compute "
            "the matrix.</div>",
            unsafe_allow_html=True,
        )
    else:
        matrix_rows, role_relevant_map, rp_granted_map = _compute_standardization(
            _roles_for_matrix, _perm_universe_in_view, view_grants,
        )
    
        if not matrix_rows:
            st.markdown(
                "<div class='jp-empty'>No matching grants for the chosen "
                "roles in the current filter set.</div>",
                unsafe_allow_html=True,
            )
        else:
            mc1, mc2, mc3 = st.columns([1, 1, 1])
            with mc1:
                show_only_dev = st.checkbox(
                    "Show only deviations (partial coverage)",
                    value=True,
                    key="matrix_only_dev",
                    help="Hide rows where coverage is either 0% (irrelevant) or 100% (perfectly standardized).",
                )
            with mc2:
                role_pick = st.selectbox(
                    "Focus role",
                    ["(all)"] + sorted({r["Role"] for r in matrix_rows}),
                    key="matrix_role_pick",
                )
            with mc3:
                perm_pick = st.selectbox(
                    "Focus permission",
                    ["(all)"] + sorted({r["Permission key"] for r in matrix_rows}),
                    key="matrix_perm_pick",
                )
    
            visible = matrix_rows
            if show_only_dev:
                visible = [r for r in visible if r["Status"] == "⚠️ partial"]
            if role_pick != "(all)":
                visible = [r for r in visible if r["Role"] == role_pick]
            if perm_pick != "(all)":
                visible = [r for r in visible if r["Permission key"] == perm_pick]
    
            # Top-level counters: how many role/permission pairs are
            # standardized vs deviating.
            tot_full     = sum(1 for r in matrix_rows if r["Status"] == "✅ all")
            tot_partial  = sum(1 for r in matrix_rows if r["Status"] == "⚠️ partial")
            tot_never    = sum(1 for r in matrix_rows if r["Status"] == "⛔ never")
            sumc = st.columns(3)
            sumc[0].markdown(
                f"<div class='jp-kpi jp-kpi-green'>"
                f"<div class='jp-kpi-label'>Fully standardized</div>"
                f"<div class='jp-kpi-num'>{tot_full}</div>"
                f"<div class='jp-kpi-sub'>role × permission pairs at 100%</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            sumc[1].markdown(
                f"<div class='jp-kpi jp-kpi-amber'>"
                f"<div class='jp-kpi-label'>Deviating</div>"
                f"<div class='jp-kpi-num'>{tot_partial}</div>"
                f"<div class='jp-kpi-sub'>partial coverage — inconsistent</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            sumc[2].markdown(
                f"<div class='jp-kpi'>"
                f"<div class='jp-kpi-label'>Never granted</div>"
                f"<div class='jp-kpi-num'>{tot_never}</div>"
                f"<div class='jp-kpi-sub'>role has 0% of permission in role-relevant schemes</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    
            st.markdown("")
            if _PANDAS:
                df = pd.DataFrame(visible)
                # Heat-tint the Coverage % column
                def _coverage_style(v):
                    try:
                        pct = float(v)
                    except Exception:
                        return ""
                    if pct >= 99.5:
                        return "background-color: #d1fae5; color: #059669; font-weight:600;"
                    if pct <= .5:
                        return "background-color: #fee2e2; color: #dc2626; font-weight:600;"
                    # Linear blend amber for partial
                    return "background-color: #fef3c7; color: #d97706; font-weight:600;"
                try:
                    styled = df.style.map(_coverage_style, subset=["Coverage %"])
                except AttributeError:
                    # pandas < 2.1 uses .applymap
                    styled = df.style.applymap(_coverage_style, subset=["Coverage %"])
                st.dataframe(styled, use_container_width=True, hide_index=True, height=420)
            else:
                for r in visible[:200]:
                    st.markdown(
                        f"- **{r['Role']}** · `{r['Permission key']}` "
                        f"({r['Permission name']}) — {r['Granted in']}/{r['Out of']} "
                        f"= **{r['Coverage %']}%** {r['Status']}"
                    )
    
            st.markdown("---")
            st.markdown("**Drill in — which schemes deviate?**")
            st.caption(
                "Pick a (role, permission) pair to see exactly which schemes "
                "are missing the grant. Click a scheme to open its full "
                "holder table in the Schemes section below."
            )
            dr_c1, dr_c2 = st.columns(2)
            d_role = dr_c1.selectbox(
                "Role",
                sorted({r["Role"] for r in matrix_rows if r["Status"] == "⚠️ partial"}) or ["(no deviations)"],
                key="drill_role",
            )
            d_perms_for_role = sorted({
                r["Permission key"] for r in matrix_rows
                if r["Status"] == "⚠️ partial" and r["Role"] == d_role
            }) or ["(no deviations)"]
            d_perm = dr_c2.selectbox(
                "Permission", d_perms_for_role,
                format_func=lambda k: f"{perm_name_by_key.get(k, k)}  ⟨{k}⟩" if k != "(no deviations)" else k,
                key="drill_perm",
            )
            if d_role != "(no deviations)" and d_perm != "(no deviations)":
                relevant_set = role_relevant_map.get(d_role, set())
                granted_set = rp_granted_map.get((d_role, d_perm), set())
                missing_set = relevant_set - granted_set
    
                mc1, mc2 = st.columns(2)
                with mc1:
                    st.markdown(
                        f"<div class='jp-banner jp-banner-ok'>"
                        f"<b>{len(granted_set)} scheme(s)</b> grant `{d_perm}` "
                        f"to <span class='jp-pill jp-role'>{d_role}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    for sid in sorted(granted_set):
                        name = schemes_by_id.get(sid, {}).get("name", str(sid))
                        cov = coverage_by_scheme.get(sid, {})
                        projs = scheme_to_projects.get(sid, [])
                        companies = sorted({project_to_company.get(p["key"], "(unmapped)") for p in projs})
                        st.markdown(
                            f"- **{name}**  "
                            f"<span class='jp-pill jp-mono'>id {sid}</span>  "
                            f"<span class='jp-pill'>{cov.get('covered', 0)}/{total_permission_count} perms</span>  "
                            + " ".join(f"<span class='jp-pill jp-teal'>{c}</span>" for c in companies),
                            unsafe_allow_html=True,
                        )
                with mc2:
                    st.markdown(
                        f"<div class='jp-banner jp-banner-red'>"
                        f"<b>{len(missing_set)} scheme(s)</b> are missing "
                        f"<code>{d_perm}</code> for <span class='jp-pill jp-role'>{d_role}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    for sid in sorted(missing_set):
                        name = schemes_by_id.get(sid, {}).get("name", str(sid))
                        cov = coverage_by_scheme.get(sid, {})
                        projs = scheme_to_projects.get(sid, [])
                        companies = sorted({project_to_company.get(p["key"], "(unmapped)") for p in projs})
                        cols = st.columns([5, 1])
                        cols[0].markdown(
                            f"- **{name}**  "
                            f"<span class='jp-pill jp-mono'>id {sid}</span>  "
                            f"<span class='jp-pill'>{cov.get('covered', 0)}/{total_permission_count} perms</span>  "
                            + " ".join(f"<span class='jp-pill jp-teal'>{c}</span>" for c in companies),
                            unsafe_allow_html=True,
                        )
                        # Quick-fix: pop a confirm popover to add the missing grant
                        # to the role's preferred group (first group in
                        # groups_for_role). Falls back to user grant prompt
                        # if the role has no LDAP-group mapping.
                        if ADMIN:
                            with cols[1].popover("Fix", use_container_width=True):
                                role_groups = groups_for_role(d_role)
                                if role_groups:
                                    target_group = st.selectbox(
                                        "Grant to which role group?",
                                        role_groups,
                                        key=f"fix_{sid}_{d_role}_{d_perm}",
                                    )
                                    st.markdown(
                                        f"<div class='jp-banner jp-banner-info'>"
                                        f"<b>Confirm:</b> grant "
                                        f"<span class='jp-pill jp-mono'>{d_perm}</span> "
                                        f"to group <b>{target_group}</b> on "
                                        f"<b>{name}</b>.</div>",
                                        unsafe_allow_html=True,
                                    )
                                    if st.button("Apply", key=f"fix_btn_{sid}_{d_role}_{d_perm}", type="primary"):
                                        ok, err = do_grant(
                                            scheme_id=sid, scheme_name=name,
                                            permission_key=d_perm,
                                            holder_type="group", holder_param=target_group,
                                            holder_display=target_group,
                                        )
                                        if ok:
                                            st.success("Granted.")
                                            st.rerun()
                                        else:
                                            st.error(err or "Grant failed.")
                                else:
                                    st.caption(
                                        f"Role `{d_role}` has no LDAP-group "
                                        "mapping in VALID_GROUPS. Use Quick "
                                        "grant in the filter bar to pick a "
                                        "specific holder."
                                    )


_section_standardization()


# ---------------------------------------------------------------------------
# Section: Per-holder access lens
# Wrapped as a fragment so the lens-internal revoke/grant popovers and the
# search box don't trigger a full-page rerun.
# ---------------------------------------------------------------------------
@st.fragment
def _section_lens():
    st.markdown(
        "<div class='jp-section-head'><h3>🔬 Holder access lens</h3>"
        "<span class='jp-section-sub'>Search any user or group — see exactly what "
        "they can do, why, and whether it's right for their role</span></div>",
        unsafe_allow_html=True,
    )

    ls1, ls2 = st.columns([1, 3])
    with ls1:
        lens_type = st.radio(
            "Lens for", ["user", "group"], horizontal=True, key="lens_type",
        )
    with ls2:
        lens_q = st.text_input("Search…", key="lens_q", placeholder="2+ chars")
        if lens_q and len(lens_q.strip()) >= 2:
            if lens_type == "user":
                res = search_users(lens_q)
                opts = {f"{r['display']}  ⟨{r['name']}⟩": r for r in res}
            else:
                res = search_groups(lens_q)
                opts = {r["name"]: r for r in res}
            if opts:
                pick = st.selectbox("Open", list(opts.keys()), key="lens_pick")
                if st.button("Open lens", key="lens_open"):
                    if lens_type == "user":
                        chosen = opts[pick]
                        st.session_state["focus_holder"] = {
                            "type": "user", "param": chosen["name"],
                            "display": chosen["display"] or chosen["name"],
                        }
                    else:
                        chosen = opts[pick]
                        st.session_state["focus_holder"] = {
                            "type": "group", "param": chosen["name"],
                            "display": chosen["name"],
                        }
                    st.rerun()
    
    
    # --- Render the focus holder (if any) --------------------------------------
    focus = st.session_state.get("focus_holder")
    if focus:
        htype = focus["type"]
        hparam = focus["param"]
        hdisplay = focus["display"]
        st.markdown(f"<div class='jp-holder-card'>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='jp-holder-head'>"
            f"<div>"
            f"<div class='jp-holder-name'>{'👤' if htype=='user' else '👥'} {hdisplay}</div>"
            f"<div class='jp-holder-id'>{htype}: <span class='jp-pill jp-mono jp-{htype}'>{hparam}</span></div>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    
        if htype == "user":
            # Identity card from LDAP + roles from RBAC
            info = ldap_user_info_safe(hparam)
            roles, role_sources = roles_for_user(hparam)
            idc1, idc2, idc3 = st.columns([2, 2, 2])
            with idc1:
                if info:
                    st.markdown(f"**Email** · {info.get('email') or '—'}")
                    st.markdown(f"**Title** · {info.get('title') or '—'}")
                    st.markdown(f"**Dept**  · {info.get('department') or '—'}")
                    if info.get("manager"):
                        st.markdown(f"**Manager** · {info['manager']}")
                else:
                    if _LDAP_AVAILABLE:
                        st.markdown(
                            "<span class='jp-pill jp-stray'>not found in LDAP</span>",
                            unsafe_allow_html=True,
                        )
                        st.caption(
                            "User has Jira grants but doesn't resolve in LDAP — "
                            "likely a decommissioned account. Audit + clean up."
                        )
                    else:
                        st.caption("_(LDAP unavailable in this environment)_")
            with idc2:
                st.markdown("**RBAC roles**")
                if roles:
                    st.markdown(
                        " ".join(f"<span class='jp-pill jp-role'>{r}</span>" for r in roles),
                        unsafe_allow_html=True,
                    )
                    with st.expander("Role provenance", expanded=False):
                        for s in role_sources:
                            st.caption(f"• {s}")
                else:
                    st.markdown(
                        "<span class='jp-pill jp-warn'>no roles resolved</span>",
                        unsafe_allow_html=True,
                    )
            with idc3:
                st.markdown("**LDAP groups**")
                if info:
                    gnames = sorted({_extract_cn(dn) for dn in (info.get("groups") or [])})
                    if gnames:
                        st.markdown(
                            " ".join(f"<span class='jp-pill jp-group'>{g}</span>" for g in gnames[:20]),
                            unsafe_allow_html=True,
                        )
                        if len(gnames) > 20:
                            st.caption(f"…and {len(gnames) - 20} more")
                    else:
                        st.caption("_(no group memberships)_")
                else:
                    st.caption("—")
    
            # Access map: every grant the user effectively has, with WHY
            st.markdown("---")
            st.markdown("**Effective Jira access**")
            user_groups_set = user_membership_groups(hparam)
            rows = []
            # Direct user grants
            for g in index["grants_by_user"].get(hparam, []):
                rows.append({
                    "g": g, "via_type": "direct",
                    "via": "direct user grant",
                    "stray": True,
                })
            # Group grants the user effectively gets (LDAP says they're in the group)
            for gname in user_groups_set:
                for g in index["grants_by_group"].get(gname, []):
                    rows.append({
                        "g": g, "via_type": "group",
                        "via": f"member of group <span class='jp-pill jp-group'>{gname}</span>",
                        "stray": False,
                    })
            # Also include grants to groups whose LDAP CN we couldn't verify
            # but that share the user's LDAP groups — already covered above.
    
            # Filter rows by the current scheme/project filter set
            rows = [r for r in rows if r["g"].scheme_id in filt["schemes_in_view"]]
    
            # Shadow detection: same scheme+perm appears both direct and group
            seen_key: dict[tuple[int, str], list[dict]] = {}
            for r in rows:
                k = (r["g"].scheme_id, r["g"].permission_key)
                seen_key.setdefault(k, []).append(r)
            for k, rlist in seen_key.items():
                if len(rlist) > 1:
                    # Multiple sources for the same access — flag the "direct" rows as shadow
                    for r in rlist:
                        if r["via_type"] == "direct":
                            r["shadow"] = True
                            r["covered_by"] = [x["g"].holder_param for x in rlist if x["via_type"] == "group"]
    
            if not rows:
                st.markdown(
                    "<div class='jp-empty'>This user has no Jira access in the current filter set.</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    "<div class='jp-access-row' style='font-weight:600;color:var(--jp-text-mute);"
                    "background:var(--jp-surface2);border-bottom:1px solid var(--jp-border);'>"
                    "<div>Permission</div><div>Scheme</div><div>Why</div><div></div></div>",
                    unsafe_allow_html=True,
                )
                # Sort: stray first, then by scheme + permission
                rows.sort(key=lambda r: (not r.get("stray"), r["g"].scheme_name, r["g"].permission_key))
                for r in rows:
                    g = r["g"]
                    row_class = ""
                    if r["via_type"] == "direct" and r.get("shadow"):
                        row_class = "jp-shadow-row"
                    elif r["via_type"] == "direct":
                        row_class = "jp-stray-row"
                    why_html = r["via"]
                    if r.get("shadow"):
                        why_html += (
                            f"<br><span class='jp-pill jp-warn'>shadow · also via "
                            f"{', '.join(r['covered_by'][:2])}</span>"
                        )
                    elif r["via_type"] == "direct":
                        why_html += (
                            "<br><span class='jp-pill jp-stray'>exclusive direct grant — "
                            "revoking removes the access</span>"
                        )
                    rc1, rc2, rc3, rc4 = st.columns([1.2, 1.5, 1.7, .6])
                    rc1.markdown(
                        f"<div class='jp-access-row {row_class}'>"
                        f"<span class='jp-perm-cell'>{g.permission_key}</span>"
                        f"<span></span><span></span><span></span></div>",
                        unsafe_allow_html=True,
                    )
                    rc2.markdown(
                        f"<div class='jp-access-row {row_class}'>"
                        f"<span></span>"
                        f"<span class='jp-scheme-cell'>{g.scheme_name}</span>"
                        f"<span></span><span></span></div>",
                        unsafe_allow_html=True,
                    )
                    rc3.markdown(
                        f"<div class='jp-access-row {row_class}'>"
                        f"<span></span><span></span>"
                        f"<span class='jp-why-cell'>{why_html}</span>"
                        f"<span></span></div>",
                        unsafe_allow_html=True,
                    )
                    # Action column: revoke only for direct grants the user actually owns
                    if r["via_type"] == "direct" and ADMIN:
                        with rc4.popover("⊖", use_container_width=True):
                            st.markdown(
                                f"**Confirm revoke** — drop `{g.permission_key}` "
                                f"from `{hparam}` on **{g.scheme_name}**."
                            )
                            if r.get("shadow"):
                                st.caption(f"User keeps access via {', '.join(r['covered_by'])}.")
                            else:
                                st.caption("⚠️ User has no other source for this permission. Access will be lost.")
                            if st.button("Apply", key=f"lensrv_{g.scheme_id}_{g.permission_id}", type="primary"):
                                ok, err = do_revoke(
                                    scheme_id=g.scheme_id, scheme_name=g.scheme_name,
                                    permission_id=g.permission_id, permission_key=g.permission_key,
                                    holder_type=g.holder_type, holder_param=g.holder_param,
                                    holder_display=g.holder_display,
                                )
                                if ok:
                                    st.success("Revoked.")
                                    st.rerun()
                                else:
                                    st.error(err or "Revoke failed.")
    
            # Inline grant for this user
            st.markdown("---")
            with st.popover("➕ Grant another permission to this user", disabled=not ADMIN):
                st.caption(
                    "Heads up: organizational policy is to grant access via "
                    "LDAP groups. Use this only when the user genuinely needs an "
                    "exception."
                )
                sid_pick = st.selectbox(
                    "Scheme", list(schemes_by_id.keys()),
                    format_func=lambda sid: schemes_by_id[sid]["name"],
                    key=f"lensgrant_scheme_{hparam}",
                )
                perm_pick = st.selectbox(
                    "Permission", perm_keys_sorted,
                    format_func=lambda k: f"{perm_name_by_key.get(k,k)} ⟨{k}⟩",
                    key=f"lensgrant_perm_{hparam}",
                )
                st.markdown(
                    f"<div class='jp-banner jp-banner-info'>"
                    f"<b>Confirm:</b> grant <span class='jp-pill jp-mono'>{perm_pick}</span> "
                    f"to user <b>{hdisplay}</b> ⟨{hparam}⟩ "
                    f"on <b>{schemes_by_id[sid_pick]['name']}</b>."
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button("Apply grant", key=f"lensgrant_apply_{hparam}", type="primary"):
                    ok, err = do_grant(
                        scheme_id=sid_pick,
                        scheme_name=schemes_by_id[sid_pick]["name"],
                        permission_key=perm_pick,
                        holder_type="user", holder_param=hparam,
                        holder_display=hdisplay,
                    )
                    if ok:
                        st.success("Granted.")
                        st.rerun()
                    else:
                        st.error(err or "Grant failed.")
    
        else:
            # ── group lens ──
            members = membership_of_group(hparam)
            rbac_roles = VALID_GROUPS.get(hparam, [])
            idc1, idc2, idc3 = st.columns([2, 2, 2])
            with idc1:
                st.markdown(f"**LDAP members** · {len(members)}")
                if members:
                    shown = sorted(members)[:30]
                    for m in shown:
                        if st.button(f"👤 {m}", key=f"glens_m_{hparam}_{m}"):
                            st.session_state["focus_holder"] = {
                                "type": "user", "param": m, "display": m,
                            }
                            st.rerun()
                    if len(members) > 30:
                        st.caption(f"…and {len(members) - 30} more")
                elif _LDAP_AVAILABLE:
                    st.markdown(
                        "<span class='jp-pill jp-stray'>0 members or LDAP miss</span>",
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        "Group has Jira grants but no LDAP members. Likely a "
                        "stale group — its grants don't actually apply to anyone."
                    )
                else:
                    st.caption("_(LDAP unavailable)_")
            with idc2:
                st.markdown("**RBAC roles** (from VALID_GROUPS)")
                if rbac_roles:
                    st.markdown(
                        " ".join(f"<span class='jp-pill jp-role'>{r}</span>" for r in rbac_roles),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        "<span class='jp-pill jp-warn'>group is granted Jira access but has no role mapping in VALID_GROUPS</span>",
                        unsafe_allow_html=True,
                    )
            with idc3:
                st.markdown("**Grants**")
                g_grants = [
                    g for g in index["grants_by_group"].get(hparam, [])
                    if g.scheme_id in filt["schemes_in_view"]
                ]
                st.metric("Grant rows", len(g_grants))
                st.metric("Schemes touched", len({g.scheme_id for g in g_grants}))
    
            st.markdown("---")
            st.markdown("**Grants made to this group**")
            g_grants_view = [
                g for g in index["grants_by_group"].get(hparam, [])
                if g.scheme_id in filt["schemes_in_view"]
            ]
            if not g_grants_view:
                st.markdown(
                    "<div class='jp-empty'>No grants for this group in the current filter set.</div>",
                    unsafe_allow_html=True,
                )
            else:
                by_scheme: dict[int, list[Grant]] = {}
                for g in g_grants_view:
                    by_scheme.setdefault(g.scheme_id, []).append(g)
                for sid in sorted(by_scheme.keys()):
                    gs = by_scheme[sid]
                    st.markdown(f"**{gs[0].scheme_name}**  <span class='jp-pill jp-mono'>id {sid}</span>", unsafe_allow_html=True)
                    for g in gs:
                        rc1, rc2 = st.columns([5, 1])
                        rc1.markdown(
                            f"<div class='jp-access-row'>"
                            f"<span class='jp-perm-cell'>{g.permission_key}</span>"
                            f"<span class='jp-scheme-cell'>{perm_name_by_key.get(g.permission_key, '')}</span>"
                            f"<span class='jp-why-cell'>granted to group · applies to {len(members)} LDAP member(s)</span>"
                            f"<span></span></div>",
                            unsafe_allow_html=True,
                        )
                        if ADMIN:
                            with rc2.popover("⊖", use_container_width=True):
                                st.markdown(
                                    f"**Confirm revoke:** drop `{g.permission_key}` "
                                    f"from group `{hparam}` on **{g.scheme_name}**."
                                )
                                st.caption(f"Will affect {len(members)} LDAP member(s).")
                                if st.button("Apply", key=f"grpx_{sid}_{g.permission_id}", type="primary"):
                                    ok, err = do_revoke(
                                        scheme_id=g.scheme_id, scheme_name=g.scheme_name,
                                        permission_id=g.permission_id, permission_key=g.permission_key,
                                        holder_type=g.holder_type, holder_param=g.holder_param,
                                        holder_display=g.holder_display,
                                    )
                                    if ok:
                                        st.success("Revoked.")
                                        st.rerun()
                                    else:
                                        st.error(err or "Revoke failed.")
    
            st.markdown("---")
            with st.popover("➕ Grant another permission to this group", disabled=not ADMIN):
                sid_pick = st.selectbox(
                    "Scheme", list(schemes_by_id.keys()),
                    format_func=lambda sid: schemes_by_id[sid]["name"],
                    key=f"glensgrant_scheme_{hparam}",
                )
                perm_pick = st.selectbox(
                    "Permission", perm_keys_sorted,
                    format_func=lambda k: f"{perm_name_by_key.get(k,k)} ⟨{k}⟩",
                    key=f"glensgrant_perm_{hparam}",
                )
                st.markdown(
                    f"<div class='jp-banner jp-banner-info'>"
                    f"<b>Confirm:</b> grant <span class='jp-pill jp-mono'>{perm_pick}</span> "
                    f"to group <b>{hparam}</b> "
                    f"on <b>{schemes_by_id[sid_pick]['name']}</b>. "
                    f"Will apply to {len(members)} LDAP member(s)."
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button("Apply grant", key=f"glensgrant_apply_{hparam}", type="primary"):
                    ok, err = do_grant(
                        scheme_id=sid_pick,
                        scheme_name=schemes_by_id[sid_pick]["name"],
                        permission_key=perm_pick,
                        holder_type="group", holder_param=hparam,
                        holder_display=hparam,
                    )
                    if ok:
                        st.success("Granted.")
                        st.rerun()
                    else:
                        st.error(err or "Grant failed.")
    
        if st.button("Close lens", key="close_lens"):
            st.session_state["focus_holder"] = None
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.markdown(
            "<div class='jp-empty'>Pick a user or group above to open its access lens.</div>",
            unsafe_allow_html=True,
        )


_section_lens()


# ---------------------------------------------------------------------------
# Section: Scheme / project explorer
# Wrapped as a fragment so the scheme/project pickers and per-row Lens
# buttons don't trigger a full-page rerun.
# ---------------------------------------------------------------------------
@st.fragment
def _section_explorer():
    st.markdown(
        "<div class='jp-section-head'><h3>📋 Schemes &amp; projects</h3>"
        "<span class='jp-section-sub'>Pick a scheme to see every holder; click a "
        "holder to open it in the lens above</span></div>",
        unsafe_allow_html=True,
    )

    ec1, ec2 = st.columns([1, 1])
    with ec1:
        # Searchable dropdown — Streamlit's selectbox filters as you type
        sx_scheme = st.selectbox(
            "Scheme",
            [None] + list(schemes_by_id.keys()),
            format_func=lambda sid: (
                "— select —" if sid is None else
                f"{schemes_by_id[sid]['name']}  · "
                f"{coverage_by_scheme.get(sid, {}).get('covered', 0)}/"
                f"{total_permission_count} perms  · id {sid}"
            ),
            key="explore_scheme",
        )
    with ec2:
        sx_project = st.selectbox(
            "Project",
            [None] + all_project_keys,
            format_func=lambda k: (
                "— select —" if k is None
                else f"{k}  · {project_to_company.get(k, '(unmapped)')}"
            ),
            key="explore_project",
        )
    
    resolved_sid: int | None = None
    if sx_project and not sx_scheme:
        resolved_sid = project_to_scheme_id.get(sx_project)
    elif sx_scheme is not None:
        resolved_sid = sx_scheme
    
    if resolved_sid is not None:
        sname = schemes_by_id.get(resolved_sid, {}).get("name", str(resolved_sid))
        sgrants = index["grants_by_scheme"].get(resolved_sid, [])
        projects_for_this = scheme_to_projects.get(resolved_sid, [])
        cov = coverage_by_scheme.get(resolved_sid, {})
        companies_for_this = sorted({
            project_to_company.get(p["key"], "(unmapped)") for p in projects_for_this
        })
        sec1, sec2, sec3, sec4 = st.columns(4)
        sec1.markdown(
            f"**Scheme · {sname}**  <span class='jp-pill jp-mono'>id {resolved_sid}</span>",
            unsafe_allow_html=True,
        )
        sec2.metric("Grants", len(sgrants))
        sec3.metric(
            "Permissions covered",
            f"{cov.get('covered', 0)} / {total_permission_count}",
            f"{(cov.get('pct', 0) * 100):.0f}% of catalog",
        )
        sec4.metric("Projects bound", len(projects_for_this))
        if companies_for_this:
            st.markdown(
                "🏢 " + " ".join(f"<span class='jp-pill jp-teal'>{c}</span>" for c in companies_for_this),
                unsafe_allow_html=True,
            )
        if projects_for_this:
            st.markdown(
                " ".join(
                    f"<span class='jp-pill jp-info'>{p['key']} · {p['name']}"
                    f"  · {project_to_company.get(p['key'], '(unmapped)')}</span>"
                    for p in projects_for_this
                ),
                unsafe_allow_html=True,
            )
        # Missing permissions in this scheme — actionable hint
        missing = sorted(set(perm_keys_sorted) - set(cov.get("perm_keys", [])))
        if missing:
            with st.expander(
                f"⚠️  {len(missing)} permission(s) in the Jira catalog have NO holder in this scheme",
                expanded=False,
            ):
                st.caption(
                    "These permission keys exist on the instance but no user, "
                    "group, or role can exercise them on projects bound to this "
                    "scheme. Often fine (e.g. Service Desk perms on a software "
                    "project) — but worth a glance."
                )
                st.markdown(
                    " ".join(
                        f"<span class='jp-pill jp-mono'>{k}</span>"
                        f" <span style='color:var(--jp-text-mute);font-size:.72rem;'>{perm_name_by_key.get(k, '')}</span><br>"
                        for k in missing
                    ),
                    unsafe_allow_html=True,
                )
    
        # Aggregate by holder so the table reads vertically
        holder_rows: dict[tuple[str, str], list[Grant]] = {}
        for g in sgrants:
            holder_rows.setdefault((g.holder_type, g.holder_param), []).append(g)
    
        st.markdown("---")
        st.markdown(
            "<div class='jp-access-row' style='font-weight:600;color:var(--jp-text-mute);background:var(--jp-surface2);'>"
            "<div>Holder</div><div>Permissions</div><div>Notes</div><div></div></div>",
            unsafe_allow_html=True,
        )
        for (htype, hparam), glist in sorted(holder_rows.items(), key=lambda kv: (kv[0][0], kv[0][1].lower())):
            # Per-row notes. We DO NOT call LDAP per row here — that would do
            # N round-trips for every scheme view and stall the page. The
            # holder lens verifies LDAP existence on demand when you click
            # into a specific holder.
            flags = []
            if htype == "user":
                flags.append("<span class='jp-pill jp-stray'>direct grant (policy: by group)</span>")
            elif htype == "group":
                if VALID_GROUPS.get(hparam):
                    flags.append(
                        f"<span class='jp-pill jp-role'>roles: "
                        f"{', '.join(VALID_GROUPS[hparam])}</span>"
                    )
            else:
                flags.append(f"<span class='jp-pill'>{htype}</span>")
    
            glist_display = " ".join(
                f"<span class='jp-pill jp-mono'>{g.permission_key}</span>" for g in glist
            )
            c_h, c_p, c_n, c_a = st.columns([1.2, 2.5, 1.5, .6])
            c_h.markdown(
                f"{'👤' if htype=='user' else ('👥' if htype=='group' else '•')}  "
                f"**{glist[0].holder_display}**  <br><span class='jp-pill jp-mono'>{hparam}</span>",
                unsafe_allow_html=True,
            )
            c_p.markdown(glist_display, unsafe_allow_html=True)
            c_n.markdown(" ".join(flags), unsafe_allow_html=True)
            if htype in ("user", "group"):
                if c_a.button("Lens", key=f"slens_{resolved_sid}_{htype}_{hparam}", use_container_width=True):
                    st.session_state["focus_holder"] = {
                        "type": htype, "param": hparam, "display": glist[0].holder_display,
                    }
                    st.rerun()
            st.divider()


_section_explorer()


# ---------------------------------------------------------------------------
# Section: Audit log (last 200 events)
# Wrapped as a fragment + the query is cached for 30 s so the log opens
# instantly on subsequent renders.
# ---------------------------------------------------------------------------
@st.fragment
def _section_audit():
    with st.expander("📋 Audit log — recent access changes", expanded=False):
        if not _schema_ok:
            st.caption("Postgres unavailable.")
        else:
            audit_rows, aerr = db_audit_query(limit=200)
            if aerr:
                st.error(aerr)
            elif not audit_rows:
                st.markdown(
                    "<div class='jp-empty'>No access changes recorded yet.</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    "<div class='jp-audit-row' style='font-weight:600;color:var(--jp-text-mute);'>"
                    "<div>Timestamp</div><div>Action</div><div>Status</div>"
                    "<div>Detail</div><div>Scheme</div></div>",
                    unsafe_allow_html=True,
                )
                for r in audit_rows:
                    ts = r["ts"].strftime("%Y-%m-%d %H:%M:%S") if hasattr(r["ts"], "strftime") else str(r["ts"])
                    action = r["action"]
                    pill = f"<span class='jp-pill {'jp-ok' if action=='grant' else 'jp-stray'}'>{action}</span>"
                    status_html = (
                        "<span class='jp-status-ok'>✓</span>"
                        if r["ok"]
                        else f"<span class='jp-status-err'>✗ {r.get('status_code') or 'err'}</span>"
                    )
                    detail = (
                        f"<code>{r['permission_key']}</code> · "
                        f"{r['holder_type']} <b>{r.get('holder_display') or r['holder_param']}</b> "
                        f"<span style='color:var(--jp-text-mute);'>(by {r['actor']})</span>"
                    )
                    if not r["ok"] and r.get("error"):
                        detail += f"<br><span style='color:var(--jp-red);font-size:.7rem;'>{str(r['error'])[:200]}</span>"
                    st.markdown(
                        f"<div class='jp-audit-row'>"
                        f"<div class='jp-ts'>{ts}</div>"
                        f"<div>{pill}</div>"
                        f"<div>{status_html}</div>"
                        f"<div>{detail}</div>"
                        f"<div>{r.get('scheme_name') or r['scheme_id']}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                # Export
                ec1x, ec2x = st.columns(2)
                ec1x.download_button(
                    "⬇ JSON",
                    data=json.dumps(audit_rows, default=str, indent=2),
                    file_name=f"jira-access-audit-{int(time.time())}.json",
                    mime="application/json",
                    use_container_width=True,
                )
                buf = io.StringIO()
                fieldnames = list(audit_rows[0].keys())
                w = csv.DictWriter(buf, fieldnames=fieldnames)
                w.writeheader()
                for r in audit_rows:
                    w.writerow({k: ("" if v is None else (json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v))) for k, v in r.items()})
                ec2x.download_button(
                    "⬇ CSV", data=buf.getvalue(),
                    file_name=f"jira-access-audit-{int(time.time())}.csv",
                    mime="text/csv", use_container_width=True,
                )


_section_audit()
