"""
Jira Permission Schemes — One-Stop Mass Console

A faster, smarter, audited alternative to the native Jira DC permission-scheme UI:

  📊 Overview        — instance-wide stats: schemes, grants, holders, top users / groups
  🔭 Browse          — full grant table per scheme + project-binding lookup
  🔍 Discrepancies   — dead schemes, duplicate schemes, shadow grants, orphan holders
  👥 Teams           — saved named groupings (users + groups) reused as filters
  ➕ Grant           — one or many holders × N permissions × M schemes
  ➖ Revoke          — pivot to "where does X have anything?" and tick to revoke
  ⇄ Copy / Move     — clone or hand-off all grants from holder A to holder B
  🔎 Locate          — cross-scheme search for a single holder
  🔐 Approvals       — DB-persisted approval queue (self-approve OR two-person)
  📋 Audit           — every write this page made, queryable from Postgres

Backends:
  • Jira DC REST API v2 — credentials via the project's VaultClient
    pattern (``vc.read_all_nested_secrets("jira")``)
  • Postgres — same vault entry & connection pattern as cicd_dashboard.py
    (``vc.read_all_nested_secrets("postgres")``). Bootstraps the three
    tables (``jira_perm_approvals``, ``jira_perm_audit``, ``jira_perm_teams``)
    automatically on first use.

Every Jira write goes through a DB approval gate first; nothing is sent to
Jira until an approval row reaches status='approved'. Every actual call is
written to the audit table with actor, target, ok/err, status code.
"""

from __future__ import annotations

import os
import io
import csv
import json
import time
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Any

import requests
from requests.auth import HTTPBasicAuth
import streamlit as st

# Project-internal modules — present in the production env, absent locally.
# Fall through to env-var / no-op fallbacks so the page is still runnable
# from a dev box.
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

# Postgres driver — psycopg v3 preferred, v2 fallback. Mirrors the pattern
# in cicd_dashboard.py so the page lights up on either deployment.
try:
    import psycopg as _psycopg  # type: ignore  # v3
    _PSYCOPG_VARIANT = "v3"
    _POSTGRES_AVAILABLE = True
except ImportError:
    try:
        import psycopg2 as _psycopg  # type: ignore
        _PSYCOPG_VARIANT = "v2"
        _POSTGRES_AVAILABLE = True
    except ImportError:
        _psycopg = None  # type: ignore
        _PSYCOPG_VARIANT = ""
        _POSTGRES_AVAILABLE = False

# Plotly / pandas — used for the Overview tab charts. Optional; tab degrades
# to plain markdown counters if missing.
try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore
try:
    import plotly.express as px
    import plotly.graph_objects as go
    _PLOTLY = True
except ImportError:
    px = None  # type: ignore
    go = None  # type: ignore
    _PLOTLY = False


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
# Page config + styling.
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
.jp-header .jp-actor {
    font-size: .78rem; color: var(--jp-text-dim);
    padding: .15rem .55rem; background: var(--jp-accent-lt);
    border: 1px solid #b3d4ff; border-radius: 4px;
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
.jp-pill.jp-purple { background: var(--jp-purple-lt); color: var(--jp-purple); border-color: #ddd6fe; }
.jp-pill.jp-teal   { background: var(--jp-teal-lt);   color: var(--jp-teal); border-color: #99f6e4; }

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

/* Stats — bright "big number" cards on the Overview tab */
.jp-stat {
    background: var(--jp-surface); border: 1px solid var(--jp-border);
    border-radius: 10px; padding: .9rem 1rem; height: 100%;
}
.jp-stat .jp-stat-label {
    font-size: .72rem; text-transform: uppercase; letter-spacing: .05em;
    color: var(--jp-text-mute); margin-bottom: .2rem;
}
.jp-stat .jp-stat-num {
    font-family: var(--jp-mono); font-size: 1.9rem; font-weight: 700;
    line-height: 1.1; color: var(--jp-text);
}
.jp-stat .jp-stat-sub {
    font-size: .76rem; color: var(--jp-text-dim); margin-top: .2rem;
}
.jp-stat.jp-stat-accent { border-left: 4px solid var(--jp-accent); }
.jp-stat.jp-stat-green  { border-left: 4px solid var(--jp-green); }
.jp-stat.jp-stat-amber  { border-left: 4px solid var(--jp-amber); }
.jp-stat.jp-stat-purple { border-left: 4px solid var(--jp-purple); }
.jp-stat.jp-stat-teal   { border-left: 4px solid var(--jp-teal); }

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

/* Discrepancy badges */
.jp-disc-card {
    background: var(--jp-surface); border: 1px solid var(--jp-border);
    border-radius: 10px; padding: .8rem 1rem; margin-bottom: .8rem;
    border-left: 4px solid var(--jp-amber);
}
.jp-disc-card.jp-sev-high { border-left-color: var(--jp-red); }
.jp-disc-card.jp-sev-info { border-left-color: var(--jp-accent); }
.jp-disc-card .jp-disc-title { font-weight: 600; color: var(--jp-text); }
.jp-disc-card .jp-disc-sub   { font-size: .78rem; color: var(--jp-text-mute); margin-top: .2rem; }

/* Approval card */
.jp-approval {
    background: var(--jp-surface); border: 1px solid var(--jp-border);
    border-radius: 10px; padding: .9rem 1.1rem; margin-bottom: .7rem;
    border-left: 4px solid var(--jp-amber);
}
.jp-approval.jp-st-approved { border-left-color: var(--jp-green); }
.jp-approval.jp-st-rejected { border-left-color: var(--jp-red); }
.jp-approval.jp-st-executed { border-left-color: var(--jp-accent); }
.jp-approval.jp-st-failed   { border-left-color: var(--jp-red); }
.jp-approval.jp-st-partial  { border-left-color: var(--jp-amber); }

.jp-audit-row {
    display: grid;
    grid-template-columns: 150px 70px 80px 1fr 140px;
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

/* Team chip strip */
.jp-team-chip {
    display: inline-block; padding: .15rem .5rem; margin: .15rem .15rem 0 0;
    background: var(--jp-teal-lt); color: var(--jp-teal);
    border: 1px solid #99f6e4; border-radius: 4px;
    font-size: .72rem; font-family: var(--jp-mono);
}
.jp-team-chip.jp-user  { background: var(--jp-accent-lt); color: var(--jp-accent); border-color: #b3d4ff; }
.jp-team-chip.jp-group { background: var(--jp-purple-lt); color: var(--jp-purple); border-color: #ddd6fe; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Identity + admin gate. Canonical role source is st.session_state.user_roles
# (dict). username / email come from the auth shell session.
# ---------------------------------------------------------------------------
def _whoami() -> str:
    """Current operator's identifier. Prefer username, fall back to email,
    finally to OS USER. Used as the actor field on every audit row and as
    the requester on every approval."""
    for k in ("username", "user"):
        v = (st.session_state.get(k) or "").strip() if isinstance(st.session_state.get(k), str) else ""
        if v:
            return v
    v = (st.session_state.get("email") or "").strip() if isinstance(st.session_state.get("email"), str) else ""
    if v:
        return v
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
# Postgres — connection + schema bootstrap. Mirrors cicd_dashboard.py:
# credentials live at vault path "postgres" with the standard keys.
# ---------------------------------------------------------------------------
POSTGRES_VAULT_PATH = os.environ.get("JIRA_PERMS_PG_VAULT_PATH", "postgres").strip()
POSTGRES_CONNECT_TIMEOUT = 10
POSTGRES_QUERY_TTL = 60          # short — approvals/audit move fast
POSTGRES_HISTORY_LIMIT = 500     # audit log default ceiling


@st.cache_data(ttl=600, show_spinner=False)
def _postgres_creds() -> dict:
    """Resolve Postgres credentials from vault. Empty dict means
    'unconfigured' — DB-backed features render an empty-state instead of
    crashing."""
    if not VaultClient:
        # Local-dev fallback via env vars (mirrors JiraAPI's pattern).
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
    """Open a fresh Postgres connection. Returns (conn, error_str). Caller
    is responsible for close. We don't pool / cache the connection — these
    are read-or-tiny-write paths and a stale connection from psycopg2 in a
    Streamlit rerun is more pain than the per-call connect overhead."""
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
            host=creds["host"],
            port=_port,
            dbname=creds["database"],
            user=creds["username"],
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


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jira_perm_approvals (
    id              BIGSERIAL PRIMARY KEY,
    ts_requested    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ts_decided      TIMESTAMPTZ,
    ts_executed     TIMESTAMPTZ,
    requester       TEXT        NOT NULL,
    approver        TEXT,
    status          TEXT        NOT NULL CHECK (status IN
                       ('pending','approved','rejected','executed','partial','failed','cancelled')),
    mode            TEXT        NOT NULL DEFAULT 'self',
    op_count        INTEGER     NOT NULL,
    grant_count     INTEGER     NOT NULL,
    revoke_count    INTEGER     NOT NULL,
    schemes_touched INTEGER     NOT NULL,
    reason          TEXT,
    decision_note   TEXT,
    ops             JSONB       NOT NULL,
    exec_summary    JSONB
);
CREATE INDEX IF NOT EXISTS jira_perm_approvals_status_idx
    ON jira_perm_approvals (status, ts_requested DESC);

CREATE TABLE IF NOT EXISTS jira_perm_audit (
    id              BIGSERIAL PRIMARY KEY,
    approval_id     BIGINT REFERENCES jira_perm_approvals(id) ON DELETE SET NULL,
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

CREATE TABLE IF NOT EXISTS jira_perm_teams (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL UNIQUE,
    description TEXT,
    members     JSONB       NOT NULL,
    created_by  TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _bootstrap_schema() -> tuple[bool, str]:
    """Idempotent CREATE TABLE IF NOT EXISTS for our three tables. Cached
    once per session — schema doesn't change at runtime."""
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


def _pg_json(value) -> str:
    """Serialise to JSON for JSONB columns. psycopg v3 accepts dicts
    directly via the Json adapter but the SQL-as-text path is the same
    across v2 and v3, so we stringify for portability."""
    return json.dumps(value, default=str)


# --- Approvals -------------------------------------------------------------

def db_create_approval_request(
    *,
    requester: str,
    mode: str,                   # "self" | "two-person"
    ops: list[dict],
    reason: str,
) -> tuple[int | None, str]:
    """Insert a new approval request in status='pending'. Returns
    (approval_id, error)."""
    conn, err = _pg_connect()
    if err:
        return None, err
    try:
        grants = sum(1 for o in ops if o.get("action") == "grant")
        revokes = sum(1 for o in ops if o.get("action") == "revoke")
        schemes = len({o.get("scheme_id") for o in ops})
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jira_perm_approvals
                  (requester, mode, status, op_count, grant_count, revoke_count,
                   schemes_touched, reason, ops)
                VALUES (%s, %s, 'pending', %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (requester, mode, len(ops), grants, revokes, schemes, reason or None, _pg_json(ops)),
            )
            row = cur.fetchone()
            return int(row[0]), ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_decide_approval(
    approval_id: int,
    *,
    approver: str,
    decision: str,         # "approved" | "rejected" | "cancelled"
    note: str,
) -> tuple[bool, str]:
    conn, err = _pg_connect()
    if err:
        return False, err
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jira_perm_approvals
                SET status = %s, approver = %s, decision_note = %s, ts_decided = NOW()
                WHERE id = %s AND status = 'pending'
                """,
                (decision, approver, note or None, approval_id),
            )
            if cur.rowcount == 0:
                return False, "request not pending or not found"
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_mark_executed(
    approval_id: int,
    *,
    final_status: str,     # 'executed' | 'partial' | 'failed'
    exec_summary: dict,
) -> tuple[bool, str]:
    conn, err = _pg_connect()
    if err:
        return False, err
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jira_perm_approvals
                SET status = %s, ts_executed = NOW(), exec_summary = %s::jsonb
                WHERE id = %s
                """,
                (final_status, _pg_json(exec_summary), approval_id),
            )
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_load_approval(approval_id: int) -> tuple[dict | None, str]:
    conn, err = _pg_connect()
    if err:
        return None, err
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, ts_requested, ts_decided, ts_executed, requester,
                       approver, status, mode, op_count, grant_count, revoke_count,
                       schemes_touched, reason, decision_note, ops, exec_summary
                FROM jira_perm_approvals WHERE id = %s
                """,
                (approval_id,),
            )
            row = cur.fetchone()
            if not row:
                return None, "not found"
            cols = [d[0] for d in cur.description]
            rec = dict(zip(cols, row))
            # Normalise JSONB → python objects (driver may return either)
            for k in ("ops", "exec_summary"):
                v = rec.get(k)
                if isinstance(v, (str, bytes, bytearray)):
                    try:
                        rec[k] = json.loads(v)
                    except Exception:
                        rec[k] = None
            return rec, ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_list_approvals(
    *,
    statuses: list[str] | None = None,
    limit: int = 100,
) -> tuple[list[dict], str]:
    conn, err = _pg_connect()
    if err:
        return [], err
    try:
        with conn.cursor() as cur:
            if statuses:
                cur.execute(
                    """
                    SELECT id, ts_requested, ts_decided, ts_executed, requester,
                           approver, status, mode, op_count, grant_count,
                           revoke_count, schemes_touched, reason, decision_note
                    FROM jira_perm_approvals
                    WHERE status = ANY(%s)
                    ORDER BY ts_requested DESC
                    LIMIT %s
                    """,
                    (statuses, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, ts_requested, ts_decided, ts_executed, requester,
                           approver, status, mode, op_count, grant_count,
                           revoke_count, schemes_touched, reason, decision_note
                    FROM jira_perm_approvals
                    ORDER BY ts_requested DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            return rows, ""
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --- Audit -----------------------------------------------------------------

def db_audit_insert_many(rows: list[dict]) -> tuple[int, str]:
    """Bulk-insert audit rows. Returns (inserted_count, error)."""
    if not rows:
        return 0, ""
    conn, err = _pg_connect()
    if err:
        return 0, err
    try:
        with conn.cursor() as cur:
            inserted = 0
            for r in rows:
                cur.execute(
                    """
                    INSERT INTO jira_perm_audit
                      (approval_id, actor, action, scheme_id, scheme_name,
                       permission_key, holder_type, holder_param, holder_display,
                       ok, status_code, error)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        r.get("approval_id"),
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
                inserted += 1
            return inserted, ""
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_audit_query(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    actor: str = "",
    action: str = "",
    scheme_id: int | None = None,
    holder: str = "",
    ok: str = "any",     # 'any' | 'ok' | 'err'
    text: str = "",
    limit: int = 500,
) -> tuple[list[dict], str]:
    conn, err = _pg_connect()
    if err:
        return [], err
    where = ["1=1"]
    args: list[Any] = []
    if since:
        where.append("ts >= %s"); args.append(since)
    if until:
        where.append("ts <= %s"); args.append(until)
    if actor:
        where.append("actor ILIKE %s"); args.append(f"%{actor}%")
    if action:
        where.append("action = %s"); args.append(action)
    if scheme_id:
        where.append("scheme_id = %s"); args.append(int(scheme_id))
    if holder:
        where.append("(holder_param ILIKE %s OR holder_display ILIKE %s)")
        args.extend([f"%{holder}%", f"%{holder}%"])
    if ok == "ok":
        where.append("ok = TRUE")
    elif ok == "err":
        where.append("ok = FALSE")
    if text:
        where.append("(COALESCE(error,'') ILIKE %s OR permission_key ILIKE %s OR scheme_name ILIKE %s)")
        args.extend([f"%{text}%", f"%{text}%", f"%{text}%"])
    sql = f"""
        SELECT id, approval_id, ts, actor, action, scheme_id, scheme_name,
               permission_key, holder_type, holder_param, holder_display,
               ok, status_code, error
        FROM jira_perm_audit
        WHERE {' AND '.join(where)}
        ORDER BY ts DESC LIMIT %s
    """
    args.append(int(limit))
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()], ""
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --- Teams -----------------------------------------------------------------

def db_teams_list() -> tuple[list[dict], str]:
    conn, err = _pg_connect()
    if err:
        return [], err
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, description, members, created_by, created_at, updated_at
                FROM jira_perm_teams ORDER BY name ASC
                """
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                v = r.get("members")
                if isinstance(v, (str, bytes, bytearray)):
                    try:
                        r["members"] = json.loads(v)
                    except Exception:
                        r["members"] = []
            return rows, ""
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_team_upsert(
    *,
    name: str,
    description: str,
    members: list[dict],
    created_by: str,
    team_id: int | None = None,
) -> tuple[int | None, str]:
    conn, err = _pg_connect()
    if err:
        return None, err
    try:
        with conn.cursor() as cur:
            if team_id:
                cur.execute(
                    """
                    UPDATE jira_perm_teams
                    SET name = %s, description = %s, members = %s::jsonb, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (name, description, _pg_json(members), team_id),
                )
                return team_id, ""
            cur.execute(
                """
                INSERT INTO jira_perm_teams (name, description, members, created_by)
                VALUES (%s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (name, description, _pg_json(members), created_by),
            )
            return int(cur.fetchone()[0]), ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_team_delete(team_id: int) -> tuple[bool, str]:
    conn, err = _pg_connect()
    if err:
        return False, err
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jira_perm_teams WHERE id = %s", (team_id,))
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Jira API — cached read helpers + write helper that surfaces server errors.
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _api() -> JiraAPI:
    return JiraAPI()


def _full(path: str) -> str:
    base = _api().base_url.rstrip("/")
    return f"{base}{path}" if path.startswith("/") else f"{base}/{path}"


def _jira_write(method: str, path: str, **kwargs) -> tuple[bool, dict, int | None]:
    """Write-path call: returns (ok, body, status) and never raises. We
    keep the server's error body intact for accurate audit log entries
    (Jira returns useful messages like 'permission already exists')."""
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
def fetch_all_permission_keys() -> list[dict]:
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


@st.cache_data(ttl=300, show_spinner=False)
def fetch_projects_for_scheme(scheme_id: int) -> list[dict]:
    """Walk projects to find those bound to this scheme. Cached."""
    out: list[dict] = []
    start = 0
    page = 50
    while True:
        res = _api().request(
            "GET",
            _full("/rest/api/2/project/search"),
            params={"startAt": start, "maxResults": page},
        )
        if isinstance(res, dict) and "error" in res:
            res2 = _api().request("GET", _full("/rest/api/2/project"))
            if isinstance(res2, dict) and "error" in res2:
                return []
            projects = res2 if isinstance(res2, list) else []
            for p in projects:
                ps = _api().request("GET", _full(f"/rest/api/2/project/{p['key']}/permissionscheme"))
                if isinstance(ps, dict) and int(ps.get("id") or -1) == int(scheme_id):
                    out.append({"key": p["key"], "name": p.get("name") or p["key"]})
            return out
        values = res.get("values") or []
        if not values:
            break
        for p in values:
            ps = _api().request("GET", _full(f"/rest/api/2/project/{p['key']}/permissionscheme"))
            if isinstance(ps, dict) and int(ps.get("id") or -1) == int(scheme_id):
                out.append({"key": p["key"], "name": p.get("name") or p["key"]})
        if res.get("isLast") or len(values) < page:
            break
        start += page
    return out


@st.cache_data(ttl=900, show_spinner=False)
def fetch_all_project_scheme_bindings() -> dict[int, list[dict]]:
    """One-shot pass: scheme_id → [{key, name}, …]. Cached aggressively
    since walking /project is expensive — used by Overview and
    Discrepancies tabs."""
    bindings: dict[int, list[dict]] = {}
    start = 0
    page = 50
    while True:
        res = _api().request(
            "GET",
            _full("/rest/api/2/project/search"),
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
        "GET",
        _full("/rest/api/2/user/picker"),
        params={"query": q, "maxResults": max_results, "showAvatar": False},
    )
    if isinstance(res, dict) and "error" in res:
        return []
    users = (res or {}).get("users") or []
    return [{
        "name": u.get("name") or u.get("key") or "",
        "key": u.get("key") or u.get("name") or "",
        "display": u.get("displayName") or u.get("name") or "",
        "email": u.get("emailAddress") or "",
    } for u in users]


@st.cache_data(ttl=120, show_spinner=False)
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
    return [{"name": g.get("name", ""), "html": g.get("html", "")}
            for g in (res or {}).get("groups") or []]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_group_members(group_name: str, max_members: int = 200) -> list[str]:
    """Return usernames in a group, capped at max_members. Used for
    shadow-grant detection. Capped to keep huge groups from melting the
    page."""
    out: list[str] = []
    start = 0
    page = 50
    while len(out) < max_members:
        res = _api().request(
            "GET",
            _full("/rest/api/2/group/member"),
            params={"groupname": group_name, "startAt": start, "maxResults": page},
        )
        if isinstance(res, dict) and "error" in res:
            return out
        values = res.get("values") or []
        if not values:
            break
        for u in values:
            n = u.get("name") or u.get("key") or ""
            if n:
                out.append(n)
        if res.get("isLast") or len(values) < page:
            break
        start += page
    return out


@st.cache_data(ttl=600, show_spinner=False)
def verify_user_exists(username: str) -> bool | None:
    """True / False / None (couldn't tell). Used by orphan-holder check."""
    if not username:
        return None
    res = _api().request("GET", _full("/rest/api/2/user"), params={"username": username})
    if isinstance(res, dict) and "error" in res:
        # 404 path arrives here too — distinguish by looking at the error
        # string; we can't read status from the wrapper. Be conservative.
        if "404" in str(res.get("error", "")):
            return False
        return None
    return bool(res)


@st.cache_data(ttl=600, show_spinner=False)
def verify_group_exists(group_name: str) -> bool | None:
    if not group_name:
        return None
    res = _api().request("GET", _full("/rest/api/2/group"), params={"groupname": group_name})
    if isinstance(res, dict) and "error" in res:
        if "404" in str(res.get("error", "")):
            return False
        return None
    return bool(res)


def _invalidate_jira_cache():
    fetch_all_schemes.clear()
    fetch_scheme_detail.clear()
    fetch_all_project_scheme_bindings.clear()


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------
HOLDER_TYPES = ("user", "group")


@dataclass
class Grant:
    scheme_id: int
    scheme_name: str
    permission_id: int
    permission_key: str
    holder_type: str
    holder_param: str
    holder_display: str

    def matches_holder(self, htype: str, hparam: str) -> bool:
        return self.holder_type == htype and self.holder_param == hparam


@dataclass
class PendingOp:
    action: str
    scheme_id: int
    scheme_name: str
    permission_key: str
    holder_type: str
    holder_param: str
    holder_display: str
    permission_id: int | None = None

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
            holder_type=htype, holder_param=hparam, holder_display=str(display),
        ))
    return out


def _all_grants_cached() -> tuple[list[Grant], list[dict]]:
    """Return every grant on the instance plus the list of schemes. The
    Overview, Discrepancies, and team-filter views all need this same
    walk — done once per render to keep cost down."""
    schemes = fetch_all_schemes()
    grants: list[Grant] = []
    for s in schemes:
        det = fetch_scheme_detail(int(s["id"]))
        grants.extend(_parse_grants(det))
    return grants, schemes


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------
def _ss_init():
    st.session_state.setdefault("jp_pending", [])
    st.session_state.setdefault("jp_active_team_id", None)
    st.session_state.setdefault("jp_approval_mode", "self")  # self | two-person
    st.session_state.setdefault("jp_last_submit_id", None)

_ss_init()


# ---------------------------------------------------------------------------
# Pending queue helpers
# ---------------------------------------------------------------------------
def _queue(op: PendingOp) -> bool:
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


def _execute_approved_request(approval: dict) -> tuple[int, int]:
    """Walk an approved request's ops list, hit Jira, write audit rows.
    Returns (ok_count, fail_count). Marks the approval as
    executed/partial/failed depending on the outcome."""
    ops = approval.get("ops") or []
    ok_count = 0
    fail_count = 0
    audit_rows: list[dict] = []
    touched_schemes: set[int] = set()
    for o in ops:
        action = o.get("action")
        scheme_id = int(o.get("scheme_id") or 0)
        permission_key = str(o.get("permission_key") or "")
        holder_type = str(o.get("holder_type") or "")
        holder_param = str(o.get("holder_param") or "")
        if action == "grant":
            ok, body, status = _jira_write(
                "POST",
                f"/rest/api/2/permissionscheme/{scheme_id}/permission",
                json={
                    "holder": {"type": holder_type, "parameter": holder_param},
                    "permission": permission_key,
                },
            )
        elif action == "revoke":
            pid = o.get("permission_id")
            if not pid:
                ok, body, status = False, {"error": "missing permission_id"}, None
            else:
                ok, body, status = _jira_write(
                    "DELETE",
                    f"/rest/api/2/permissionscheme/{scheme_id}/permission/{int(pid)}",
                )
        else:
            ok, body, status = False, {"error": f"unknown action {action}"}, None

        if not ok:
            err = (body.get("errorMessages") or body.get("errors") or
                   body.get("error") or body.get("raw") or body)
            err_str = json.dumps(err, default=str)[:1000] if not isinstance(err, str) else err[:1000]
        else:
            err_str = None

        audit_rows.append({
            "approval_id": approval.get("id"),
            "actor": ACTOR,
            "action": action,
            "scheme_id": scheme_id,
            "scheme_name": o.get("scheme_name"),
            "permission_key": permission_key,
            "holder_type": holder_type,
            "holder_param": holder_param,
            "holder_display": o.get("holder_display"),
            "ok": bool(ok),
            "status_code": status,
            "error": err_str,
        })
        if ok:
            ok_count += 1
        else:
            fail_count += 1
        touched_schemes.add(scheme_id)

    n_inserted, audit_err = db_audit_insert_many(audit_rows)
    if audit_err:
        st.error(f"Audit insert error: {audit_err}")

    final = ("executed" if fail_count == 0
             else ("failed" if ok_count == 0 else "partial"))
    db_mark_executed(int(approval["id"]), final_status=final, exec_summary={
        "ok": ok_count, "fail": fail_count, "audit_rows": n_inserted,
    })
    for sid in touched_schemes:
        fetch_scheme_detail.clear()
    return ok_count, fail_count


# ---------------------------------------------------------------------------
# Team filter — resolves a saved team to a list of expanded holder targets
# ---------------------------------------------------------------------------
def _team_member_predicate(members: list[dict]):
    """Return a predicate(grant) → bool that matches a grant against the
    team members. Users match by username; groups match by group name OR
    if a user-member is a member of the granted group (shallow check)."""
    user_names = {m["name"] for m in members if m.get("type") == "user"}
    group_names = {m["name"] for m in members if m.get("type") == "group"}

    def pred(g: Grant) -> bool:
        if g.holder_type == "user" and g.holder_param in user_names:
            return True
        if g.holder_type == "group" and g.holder_param in group_names:
            return True
        return False
    return pred


def _parse_team_members(raw: str) -> list[dict]:
    """One per line; `g:name` = group, `u:name` or bare = user. Dedup."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ln in (raw or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.lower().startswith("g:"):
            mtype, mname = "group", ln[2:].strip()
        elif ln.lower().startswith("u:"):
            mtype, mname = "user", ln[2:].strip()
        else:
            mtype, mname = "user", ln
        if not mname:
            continue
        key = (mtype, mname)
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": mtype, "name": mname})
    return out


def _team_form(team: dict, admin: bool):
    """Inline edit form for an existing team."""
    with st.form(f"edit_team_{team['id']}", clear_on_submit=False):
        st.markdown(f"**Editing `{team['name']}`**")
        n1, n2 = st.columns([1, 2])
        new_name = n1.text_input("Name", value=team["name"], disabled=not admin, key=f"ten_{team['id']}")
        new_desc = n2.text_input("Description", value=team.get("description") or "", disabled=not admin, key=f"ted_{team['id']}")
        existing = team.get("members") or []
        prefilled = "\n".join(
            ("g:" if m.get("type") == "group" else "") + m.get("name", "") for m in existing
        )
        new_raw = st.text_area("Members", value=prefilled, height=160, disabled=not admin, key=f"tem_{team['id']}")
        c1, c2 = st.columns(2)
        save = c1.form_submit_button("Save changes", type="primary", disabled=not admin)
        cancel = c2.form_submit_button("Cancel")
        if save:
            members = _parse_team_members(new_raw)
            if not new_name.strip() or not members:
                st.error("Name and at least one member required.")
            else:
                _, err = db_team_upsert(
                    name=new_name.strip(),
                    description=new_desc.strip(),
                    members=members,
                    created_by=team["created_by"],
                    team_id=int(team["id"]),
                )
                if err:
                    st.error(err)
                else:
                    st.session_state[f"team_edit_open_{team['id']}"] = False
                    st.success("Updated.")
                    st.rerun()
        if cancel:
            st.session_state[f"team_edit_open_{team['id']}"] = False
            st.rerun()


# ---------------------------------------------------------------------------
# Holder picker — sticky across reruns
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
            label, key=f"{key_prefix}_query",
            placeholder="Search by name… (min 2 chars)",
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
  <span class="jp-actor">👤 {ACTOR}{'  · admin' if ADMIN else ''}</span>
  <span style="margin-left:auto;font-size:.78rem;color:var(--jp-text-mute);">
    Mass console · approval-gated · DB-audited
  </span>
</div>
""",
    unsafe_allow_html=True,
)

# DB bootstrap — best-effort; surface failure non-fatally so read-only Jira
# views still work even if Postgres is unreachable.
_schema_ok, _schema_err = _bootstrap_schema()
if not _schema_ok:
    st.warning(
        f"📦 Postgres unavailable — approvals, audit history, and team "
        f"definitions won't persist this session. ({_schema_err})"
    )

if not ADMIN:
    st.warning(
        "🔒 This page is **admin-only** for writes. You can browse, search, "
        "and view stats / discrepancies in read-only mode."
    )


# ---------------------------------------------------------------------------
# Sidebar — minimal rail. Connection status, refresh, approval mode, active
# team filter, pending op count.
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Connection")
    st.caption(f"Jira: `{_host}`")
    pg_creds = _postgres_creds()
    pg_label = f"`{pg_creds.get('host','—')}/{pg_creds.get('database','—')}`" if pg_creds else "_(not configured)_"
    st.caption(f"Postgres: {pg_label}")
    if st.button("🔄 Refresh all caches", use_container_width=True):
        fetch_all_schemes.clear()
        fetch_scheme_detail.clear()
        fetch_all_permission_keys.clear()
        fetch_projects_for_scheme.clear()
        fetch_all_project_scheme_bindings.clear()
        search_users.clear()
        search_groups.clear()
        fetch_group_members.clear()
        verify_user_exists.clear()
        verify_group_exists.clear()
        _postgres_creds.clear()
        st.success("Caches cleared.")
        st.rerun()

    st.markdown("---")
    st.markdown("### Approval mode")
    st.session_state["jp_approval_mode"] = st.radio(
        "Who approves?",
        options=["self", "two-person"],
        index=0 if st.session_state["jp_approval_mode"] == "self" else 1,
        format_func=lambda x: "Self-approve" if x == "self" else "Two-person (separate approver)",
        help=(
            "Self-approve: you submit and approve in one step (typed confirm).  "
            "Two-person: the request lands in the Approvals tab; a *different* "
            "admin must approve before any Jira call is made."
        ),
        key="jp_approval_mode_radio",
    )

    st.markdown("---")
    st.markdown("### Team filter")
    teams_list, teams_err = db_teams_list() if _schema_ok else ([], "schema not ready")
    if teams_err and _schema_ok:
        st.caption(f"_team load failed: {teams_err}_")
    team_options = {0: "— None (show everything)"}
    for t in teams_list:
        team_options[int(t["id"])] = t["name"]
    cur_pick = int(st.session_state.get("jp_active_team_id") or 0)
    if cur_pick not in team_options:
        cur_pick = 0
    picked = st.selectbox(
        "Apply team filter to Overview / Browse / Locate",
        list(team_options.keys()),
        index=list(team_options.keys()).index(cur_pick),
        format_func=lambda k: team_options[k],
        key="jp_active_team_sel",
    )
    st.session_state["jp_active_team_id"] = picked if picked else None

    st.markdown("---")
    pending_n = len(st.session_state["jp_pending"])
    st.markdown(
        f"<div style='font-size:.85rem;color:var(--jp-text-dim);'>"
        f"Pending draft ops: <b>{pending_n}</b><br>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if pending_n and st.button("Clear pending draft", use_container_width=True):
        _clear_pending()
        st.rerun()


# ---------------------------------------------------------------------------
# Load core data once per rerun.
# ---------------------------------------------------------------------------
schemes = fetch_all_schemes()
schemes_by_id: dict[int, dict] = {int(s["id"]): s for s in schemes if s.get("id") is not None}
perm_catalog = fetch_all_permission_keys()
perm_keys_sorted = [p["key"] for p in perm_catalog]
perm_name_by_key = {p["key"]: p["name"] for p in perm_catalog}
perm_desc_by_key = {p["key"]: p["description"] for p in perm_catalog}

if not schemes:
    st.markdown(
        '<div class="jp-empty">No permission schemes returned. '
        'Check Vault config / Jira reachability.</div>',
        unsafe_allow_html=True,
    )
    st.stop()

# Resolve active team (if any) once per rerun.
_active_team: dict | None = None
_team_pred = None
if st.session_state.get("jp_active_team_id"):
    for t in teams_list:
        if int(t["id"]) == int(st.session_state["jp_active_team_id"]):
            _active_team = t
            _team_pred = _team_member_predicate(t.get("members") or [])
            break


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
TABS = [
    "📊 Overview",
    "🔭 Browse",
    "🔍 Discrepancies",
    "👥 Teams",
    "➕ Grant",
    "➖ Revoke",
    "⇄ Copy",
    "🔎 Locate",
    "🔐 Approvals",
    "📋 Audit",
]
(
    tab_overview, tab_browse, tab_disc, tab_teams,
    tab_grant, tab_revoke, tab_copy, tab_search,
    tab_approvals, tab_audit,
) = st.tabs(TABS)


# ===========================================================================
# Tab: Overview — instance-wide stats. Pulls the full grant set once and
# slices it every which way. Filters by active team if one is selected.
# ===========================================================================
with tab_overview:
    st.markdown("##### Instance-wide permissions overview")
    sub = (
        f"Filtered by team **{_active_team['name']}**"
        if _active_team else "Showing every scheme on the instance"
    )
    st.caption(sub)

    with st.spinner("Aggregating grants across schemes…"):
        all_grants, _ = _all_grants_cached()
        bindings = fetch_all_project_scheme_bindings()

    # Apply team filter to the grant set
    if _team_pred:
        grants_view = [g for g in all_grants if _team_pred(g)]
    else:
        grants_view = all_grants

    n_schemes = len(schemes)
    n_grants = len(grants_view)
    user_holders = {g.holder_param for g in grants_view if g.holder_type == "user"}
    group_holders = {g.holder_param for g in grants_view if g.holder_type == "group"}
    role_holders = {g.holder_param for g in grants_view if g.holder_type == "projectRole"}
    special_holders = {g.holder_type for g in grants_view if g.holder_type not in ("user", "group", "projectRole")}
    n_projects_total = sum(len(v) for v in bindings.values())
    n_schemes_bound = sum(1 for sid in schemes_by_id if bindings.get(sid))

    # ── KPI cards ───────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    cards = [
        (c1, "Permission schemes", n_schemes, f"{n_schemes_bound} bound to ≥1 project", "jp-stat-accent"),
        (c2, "Total grants", n_grants, f"avg {n_grants / max(n_schemes,1):.1f} per scheme", "jp-stat-green"),
        (c3, "Distinct users", len(user_holders), "named user holders", "jp-stat-purple"),
        (c4, "Distinct groups", len(group_holders), "group holders", "jp-stat-teal"),
        (c5, "Projects covered", n_projects_total, f"{len(bindings)} schemes have ≥1 project", "jp-stat-amber"),
    ]
    for col, label, num, sub, klass in cards:
        col.markdown(
            f"<div class='jp-stat {klass}'>"
            f"<div class='jp-stat-label'>{label}</div>"
            f"<div class='jp-stat-num'>{num:,}</div>"
            f"<div class='jp-stat-sub'>{sub}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("")

    # ── Holder-type breakdown chart ─────────────────────────────────────
    type_counts: dict[str, int] = {}
    for g in grants_view:
        type_counts[g.holder_type] = type_counts.get(g.holder_type, 0) + 1

    cA, cB = st.columns([1, 1])
    with cA:
        st.markdown("##### Holder-type breakdown")
        if _PLOTLY and type_counts:
            fig = px.bar(
                x=list(type_counts.values()),
                y=list(type_counts.keys()),
                orientation="h",
                labels={"x": "grants", "y": "holder type"},
                color=list(type_counts.keys()),
                color_discrete_map={
                    "user": "#0052cc", "group": "#7c3aed",
                    "projectRole": "#0d9488", "applicationRole": "#d97706",
                    "anyone": "#dc2626", "currentUser": "#059669",
                    "projectLead": "#7c2d12", "assignee": "#1e40af",
                    "reporter": "#be185d",
                },
            )
            fig.update_layout(
                height=260, margin=dict(l=10, r=10, t=10, b=10),
                showlegend=False, plot_bgcolor="white", paper_bgcolor="white",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            for k, v in sorted(type_counts.items(), key=lambda x: -x[1]):
                st.markdown(f"- **{k}** · {v}")

    with cB:
        st.markdown("##### Permission popularity (top 12)")
        perm_counts: dict[str, int] = {}
        for g in grants_view:
            perm_counts[g.permission_key] = perm_counts.get(g.permission_key, 0) + 1
        top_perms = sorted(perm_counts.items(), key=lambda x: -x[1])[:12]
        if _PLOTLY and top_perms:
            fig = px.bar(
                x=[v for _, v in top_perms],
                y=[k for k, _ in top_perms],
                orientation="h",
                labels={"x": "grants", "y": "permission"},
            )
            fig.update_traces(marker_color="#0052cc")
            fig.update_layout(
                height=320, margin=dict(l=10, r=10, t=10, b=10),
                plot_bgcolor="white", paper_bgcolor="white",
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            for k, v in top_perms:
                st.markdown(f"- `{k}` — {v}")

    st.markdown("")

    # ── Top groups + top users ──────────────────────────────────────────
    cG, cU = st.columns(2)
    with cG:
        st.markdown("##### Top groups by grant count")
        gc: dict[str, int] = {}
        # Distinct (scheme_id, group) pairs counted once — closer to
        # "groups granted on how many schemes". Each row in the table
        # shows grants AND distinct schemes covered.
        group_schemes: dict[str, set[int]] = {}
        for g in grants_view:
            if g.holder_type != "group":
                continue
            gc[g.holder_param] = gc.get(g.holder_param, 0) + 1
            group_schemes.setdefault(g.holder_param, set()).add(g.scheme_id)
        top_g = sorted(gc.items(), key=lambda x: -x[1])[:20]
        if not top_g:
            st.markdown("<div class='jp-empty'>No group grants in view.</div>", unsafe_allow_html=True)
        else:
            rows = []
            for gname, n in top_g:
                schemes_for_g = group_schemes[gname]
                projects_covered = set()
                for sid in schemes_for_g:
                    for p in bindings.get(sid, []):
                        projects_covered.add(p["key"])
                rows.append({
                    "Group": gname,
                    "Grants": n,
                    "Schemes": len(schemes_for_g),
                    "Projects covered": len(projects_covered),
                })
            if pd is not None:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                for r in rows:
                    st.markdown(f"- **{r['Group']}** — {r['Grants']} grants · {r['Schemes']} schemes · {r['Projects covered']} projects")

    with cU:
        st.markdown("##### Top users by grant count")
        uc: dict[str, int] = {}
        user_schemes: dict[str, set[int]] = {}
        user_display: dict[str, str] = {}
        for g in grants_view:
            if g.holder_type != "user":
                continue
            uc[g.holder_param] = uc.get(g.holder_param, 0) + 1
            user_schemes.setdefault(g.holder_param, set()).add(g.scheme_id)
            user_display[g.holder_param] = g.holder_display
        top_u = sorted(uc.items(), key=lambda x: -x[1])[:20]
        if not top_u:
            st.markdown("<div class='jp-empty'>No direct user grants in view.</div>", unsafe_allow_html=True)
        else:
            rows = []
            for uname, n in top_u:
                schemes_for_u = user_schemes[uname]
                projects_covered = set()
                for sid in schemes_for_u:
                    for p in bindings.get(sid, []):
                        projects_covered.add(p["key"])
                rows.append({
                    "User":  user_display.get(uname, uname),
                    "Login": uname,
                    "Grants": n,
                    "Schemes": len(schemes_for_u),
                    "Projects": len(projects_covered),
                })
            if pd is not None:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                for r in rows:
                    st.markdown(f"- **{r['User']}** ⟨`{r['Login']}`⟩ — {r['Grants']} / {r['Schemes']}s / {r['Projects']}p")

    # ── Scheme leaderboard ──────────────────────────────────────────────
    st.markdown("##### Schemes ranked by grant volume")
    scheme_grants: dict[int, int] = {}
    for g in grants_view:
        scheme_grants[g.scheme_id] = scheme_grants.get(g.scheme_id, 0) + 1
    rows = []
    for sid, s in schemes_by_id.items():
        rows.append({
            "Scheme":    s.get("name") or str(sid),
            "ID":        sid,
            "Grants":    scheme_grants.get(sid, 0),
            "Projects":  len(bindings.get(sid, [])),
        })
    rows.sort(key=lambda r: -r["Grants"])
    if pd is not None:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=320)
    else:
        for r in rows[:50]:
            st.markdown(f"- **{r['Scheme']}** (id {r['ID']}) — {r['Grants']} grants · {r['Projects']} projects")


# ===========================================================================
# Tab: Browse (mostly unchanged from v1, plus team-filter awareness)
# ===========================================================================
with tab_browse:
    st.markdown("##### Browse permission schemes")
    if _active_team:
        st.caption(
            f"Team filter active (**{_active_team['name']}**) — grant rows "
            f"matching team members are highlighted; the rest dimmed."
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

        with st.expander("Projects bound to this scheme", expanded=False):
            bound = fetch_projects_for_scheme(sid)
            if not bound:
                st.caption("No projects use this scheme.")
            else:
                st.markdown(
                    " ".join(f"<span class='jp-pill jp-info'>{p['key']} · {p['name']}</span>" for p in bound),
                    unsafe_allow_html=True,
                )
                st.caption(f"{len(bound)} project(s)")

        f1, f2, f3 = st.columns([2, 2, 1])
        with f1:
            perm_filter = st.multiselect(
                "Filter permissions",
                sorted({g.permission_key for g in grants}),
                key=f"browse_pf_{sid}",
            )
        with f2:
            holder_filter = st.text_input(
                "Filter holder (substring)", key=f"browse_hf_{sid}",
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
        by_perm: dict[str, list[Grant]] = {}
        for g in visible:
            by_perm.setdefault(g.permission_key, []).append(g)

        st.markdown(f"Showing **{len(visible)}** of {len(grants)} grants across **{len(by_perm)}** permissions.")

        for pkey in sorted(by_perm.keys()):
            holders = by_perm[pkey]
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
                    "projectLead": "👑", "currentUser": "🙋", "anyone": "🌐",
                }.get(g.holder_type, "•")
                dim = ""
                if _team_pred and not _team_pred(g):
                    dim = "opacity:.45;"
                rows.append(
                    f"<div class='jp-grant-row' style='{dim}'>"
                    f"<span class='jp-perm'>{g.holder_type}</span>"
                    f"<span class='jp-holder'>{badge} {g.holder_display} "
                    f"<span style='color:var(--jp-text-mute);'>⟨{g.holder_param or '—'}⟩</span></span>"
                    f"</div>"
                )
            st.markdown("".join(rows), unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)


# ===========================================================================
# Tab: Discrepancies — detect dead schemes, duplicate schemes, shadow grants,
# orphan holders. Each section is independently runnable so a slow scan
# doesn't block the cheap ones.
# ===========================================================================
with tab_disc:
    st.markdown("##### Detect misconfigurations across schemes")
    st.caption("Cheap checks run automatically; expensive ones are gated behind a button.")

    all_grants, _ = _all_grants_cached()
    bindings = fetch_all_project_scheme_bindings()

    # ── 1. Dead schemes (no project binding) ────────────────────────────
    dead = [s for s in schemes if not bindings.get(int(s["id"]))]
    st.markdown(
        f"<div class='jp-disc-card {'jp-sev-info' if not dead else ''}'>"
        f"<div class='jp-disc-title'>Dead schemes — no project bound</div>"
        f"<div class='jp-disc-sub'>{len(dead)} of {len(schemes)} scheme(s) "
        f"have zero projects pointing at them. Safe to archive or delete "
        f"if you've stopped using them.</div></div>",
        unsafe_allow_html=True,
    )
    if dead:
        with st.expander(f"Show {len(dead)} dead scheme(s)", expanded=False):
            for s in dead:
                st.markdown(f"- **{s['name']}** ⟨id {s['id']}⟩ — _{s.get('description') or 'no description'}_")

    # ── 2. Duplicate schemes (identical grant set) ──────────────────────
    grant_sig_by_scheme: dict[int, frozenset] = {}
    for g in all_grants:
        grant_sig_by_scheme.setdefault(g.scheme_id, set()).add(
            (g.permission_key, g.holder_type, g.holder_param)
        )
    sig_buckets: dict[frozenset, list[int]] = {}
    for sid, sig in grant_sig_by_scheme.items():
        fs = frozenset(sig)
        sig_buckets.setdefault(fs, []).append(sid)
    dupes = [ids for ids in sig_buckets.values() if len(ids) > 1]
    st.markdown(
        f"<div class='jp-disc-card {'jp-sev-info' if not dupes else ''}'>"
        f"<div class='jp-disc-title'>Duplicate schemes — identical grants</div>"
        f"<div class='jp-disc-sub'>{len(dupes)} group(s) of schemes share an "
        f"identical grant set. Candidates for consolidation — bind their "
        f"projects to a single scheme and delete the rest.</div></div>",
        unsafe_allow_html=True,
    )
    if dupes:
        with st.expander(f"Show {len(dupes)} duplicate group(s)", expanded=False):
            for grp in dupes:
                names = [
                    f"**{schemes_by_id.get(i, {}).get('name', i)}** (id {i})"
                    for i in grp
                ]
                projects = sum(len(bindings.get(i, [])) for i in grp)
                st.markdown(f"- {' · '.join(names)}  —  combined {projects} project(s)")

    # ── 3. Orphan holders (on-demand) ───────────────────────────────────
    st.markdown(
        "<div class='jp-disc-card'>"
        "<div class='jp-disc-title'>Orphan holders — user/group no longer exists</div>"
        "<div class='jp-disc-sub'>Verifies every distinct user / group "
        "holder against the Jira API. Slow on large instances; results "
        "cached 10 min.</div></div>",
        unsafe_allow_html=True,
    )
    if st.button("Run orphan-holder scan", key="disc_orphan_btn"):
        users_to_check = sorted({g.holder_param for g in all_grants if g.holder_type == "user"})
        groups_to_check = sorted({g.holder_param for g in all_grants if g.holder_type == "group"})
        progress = st.progress(0, text=f"Checking {len(users_to_check) + len(groups_to_check)} holder(s)…")
        orphans_user: list[str] = []
        orphans_group: list[str] = []
        unknown: list[str] = []
        total = len(users_to_check) + len(groups_to_check)
        done = 0
        for u in users_to_check:
            ok = verify_user_exists(u)
            if ok is False:
                orphans_user.append(u)
            elif ok is None:
                unknown.append(f"user:{u}")
            done += 1
            progress.progress(done / max(total, 1), text=f"Verified {done}/{total}")
        for g in groups_to_check:
            ok = verify_group_exists(g)
            if ok is False:
                orphans_group.append(g)
            elif ok is None:
                unknown.append(f"group:{g}")
            done += 1
            progress.progress(done / max(total, 1), text=f"Verified {done}/{total}")
        progress.empty()
        st.session_state["jp_orphan_scan"] = {
            "users": orphans_user, "groups": orphans_group, "unknown": unknown,
        }

    scan = st.session_state.get("jp_orphan_scan")
    if scan:
        cu, cg, ck = st.columns(3)
        cu.metric("Orphan users", len(scan["users"]))
        cg.metric("Orphan groups", len(scan["groups"]))
        ck.metric("Unknown (lookup errored)", len(scan["unknown"]))
        if scan["users"]:
            with st.expander("Orphan users", expanded=False):
                grants_by_orphan: dict[str, list[Grant]] = {}
                for g in all_grants:
                    if g.holder_type == "user" and g.holder_param in scan["users"]:
                        grants_by_orphan.setdefault(g.holder_param, []).append(g)
                for u in scan["users"]:
                    glist = grants_by_orphan.get(u, [])
                    st.markdown(f"- `{u}` — {len(glist)} stale grant(s) across {len({x.scheme_id for x in glist})} scheme(s)")
        if scan["groups"]:
            with st.expander("Orphan groups", expanded=False):
                for g in scan["groups"]:
                    st.markdown(f"- `{g}`")

    # ── 4. Shadow grants ────────────────────────────────────────────────
    st.markdown(
        "<div class='jp-disc-card'>"
        "<div class='jp-disc-title'>Shadow grants — user has both direct + via-group access</div>"
        "<div class='jp-disc-sub'>For each scheme, finds users granted "
        "a permission directly AND through a group they belong to. The "
        "direct grant is redundant — consider removing it to keep group "
        "membership as the single source of truth.</div></div>",
        unsafe_allow_html=True,
    )
    if st.button("Run shadow-grant scan", key="disc_shadow_btn"):
        # Resolve group → members for every group referenced in any grant.
        groups_used = sorted({g.holder_param for g in all_grants if g.holder_type == "group"})
        progress = st.progress(0, text=f"Resolving {len(groups_used)} group membership(s)…")
        group_to_members: dict[str, set[str]] = {}
        for i, gname in enumerate(groups_used):
            group_to_members[gname] = set(fetch_group_members(gname))
            progress.progress((i + 1) / max(len(groups_used), 1))
        progress.empty()
        # For each scheme × permission, check direct-user-grant + any
        # group-grant on same permission whose member set includes that user.
        shadows: list[tuple[int, str, str, str, str]] = []
        # (scheme_id, perm, user, via_group, display)
        by_scheme: dict[int, list[Grant]] = {}
        for g in all_grants:
            by_scheme.setdefault(g.scheme_id, []).append(g)
        for sid, gs in by_scheme.items():
            # bucket by permission
            by_perm: dict[str, dict] = {}
            for g in gs:
                slot = by_perm.setdefault(g.permission_key, {"users": [], "groups": []})
                if g.holder_type == "user":
                    slot["users"].append(g)
                elif g.holder_type == "group":
                    slot["groups"].append(g)
            for pkey, slot in by_perm.items():
                for u in slot["users"]:
                    for gr in slot["groups"]:
                        if u.holder_param in group_to_members.get(gr.holder_param, set()):
                            shadows.append((sid, pkey, u.holder_param, gr.holder_param, u.holder_display))
        st.session_state["jp_shadow_scan"] = shadows

    shadows = st.session_state.get("jp_shadow_scan")
    if shadows is not None:
        st.metric("Shadow grants detected", len(shadows))
        if shadows:
            with st.expander(f"Show {len(shadows)} shadow grant(s)", expanded=False):
                for sid, pkey, uname, gname, udisp in shadows[:300]:
                    s_name = schemes_by_id.get(sid, {}).get("name", sid)
                    st.markdown(
                        f"- **{s_name}** · `{pkey}` — `{uname}` (via group `{gname}`) — _{udisp}_"
                    )
                if len(shadows) > 300:
                    st.caption(f"…and {len(shadows) - 300} more (truncated for display).")


# ===========================================================================
# Tab: Teams — CRUD for saved holder groupings
# ===========================================================================
with tab_teams:
    st.markdown("##### Saved teams / filters")
    st.caption(
        "A *team* is a named set of users and/or groups. Once saved it "
        "shows up in the sidebar filter and narrows the Overview, Browse, "
        "and Locate tabs to that team's members."
    )
    if not _schema_ok:
        st.markdown(
            "<div class='jp-empty'>Postgres unavailable — teams can't be persisted.</div>",
            unsafe_allow_html=True,
        )
    else:
        teams_list_fresh, terr = db_teams_list()
        if terr:
            st.error(f"Load failed: {terr}")
        else:
            # Existing teams
            for t in teams_list_fresh:
                with st.container():
                    st.markdown(
                        f"<div class='jp-card'><div class='jp-card-head'>"
                        f"<div><span class='jp-title'>{t['name']}</span>"
                        f"  <span class='jp-pill'>id {t['id']}</span></div>"
                        f"<div class='jp-sub'>by {t['created_by']} · "
                        f"updated {t['updated_at'].strftime('%Y-%m-%d %H:%M') if hasattr(t['updated_at'], 'strftime') else t['updated_at']}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    if t.get("description"):
                        st.caption(t["description"])
                    members = t.get("members") or []
                    chip_html = " ".join(
                        f"<span class='jp-team-chip jp-{m.get('type','')}'>"
                        f"{'👤' if m.get('type')=='user' else '👥'} {m.get('name','')}</span>"
                        for m in members
                    )
                    st.markdown(chip_html, unsafe_allow_html=True)
                    st.caption(
                        f"{sum(1 for m in members if m.get('type')=='user')} user(s) · "
                        f"{sum(1 for m in members if m.get('type')=='group')} group(s)"
                    )

                    cc1, cc2, cc3 = st.columns([1, 1, 2])
                    if cc1.button("Edit", key=f"team_edit_{t['id']}", disabled=not ADMIN):
                        st.session_state[f"team_edit_open_{t['id']}"] = True
                    if cc2.button("Delete", key=f"team_del_{t['id']}", disabled=not ADMIN):
                        ok, err = db_team_delete(int(t["id"]))
                        if ok:
                            st.success(f"Deleted team '{t['name']}'.")
                            st.rerun()
                        else:
                            st.error(err)
                    if st.session_state.get(f"team_edit_open_{t['id']}"):
                        _team_form(t, ADMIN)
                    st.markdown("</div>", unsafe_allow_html=True)

            if not teams_list_fresh:
                st.markdown(
                    "<div class='jp-empty'>No teams saved yet — create the first one below.</div>",
                    unsafe_allow_html=True,
                )

            st.markdown("---")
            st.markdown("#### Create new team")
            if not ADMIN:
                st.info("Read-only — admin role required to save teams.")
            with st.form("new_team_form", clear_on_submit=True):
                n1, n2 = st.columns([1, 2])
                new_name = n1.text_input("Team name", placeholder="e.g. payments-devs", disabled=not ADMIN)
                new_desc = n2.text_input("Description", placeholder="optional", disabled=not ADMIN)
                st.markdown("**Members** — paste usernames + group names below, one per line. "
                            "Prefix with `g:` for groups (default is user).")
                new_members_raw = st.text_area(
                    "Members",
                    height=160,
                    placeholder="jdoe\nasmith\ng:payments-developers\ng:payments-admins",
                    disabled=not ADMIN,
                )
                submitted = st.form_submit_button("Save team", type="primary", disabled=not ADMIN)
                if submitted:
                    members = _parse_team_members(new_members_raw)
                    if not new_name.strip():
                        st.error("Team name is required.")
                    elif not members:
                        st.error("Add at least one member.")
                    else:
                        tid, err = db_team_upsert(
                            name=new_name.strip(),
                            description=new_desc.strip(),
                            members=members,
                            created_by=ACTOR,
                        )
                        if err:
                            st.error(err)
                        else:
                            st.success(f"Team #{tid} '{new_name.strip()}' saved.")
                            st.rerun()


# ===========================================================================
# Tab: Grant — bulk grant flow (unchanged shape; routes through approval)
# ===========================================================================
with tab_grant:
    st.markdown("##### Grant permissions in bulk")
    st.caption(
        "Pick one holder (or paste many), select the permissions, select the "
        "schemes. Operations queue into a draft; submitting opens an approval "
        "request in Postgres."
    )
    if not ADMIN:
        st.info("Read-only — admin role required to queue writes.")

    mode = st.radio(
        "Holder input", ["Single (search)", "Paste many"],
        horizontal=True, key="grant_mode", disabled=not ADMIN,
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
                "Type", HOLDER_TYPES,
                format_func=lambda x: {"user": "👤 Users", "group": "👥 Groups"}[x],
                key="grant_paste_type",
            )
        with c2:
            pasted = st.text_area(
                "One name per line", key="grant_paste_text", height=110,
                placeholder="jdoe\nasmith\nfgarcia",
            )
        seen = set()
        for ln in [ln.strip() for ln in (pasted or "").splitlines() if ln.strip()]:
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
        )
    with c2:
        all_scheme_labels = [f"{s['name']}  ⟨id {s['id']}⟩" for s in schemes]
        scheme_sel = st.multiselect(
            "Schemes to apply to", all_scheme_labels, key="grant_schemes",
        )
        if st.button("Select all schemes", key="grant_all_schemes"):
            st.session_state["grant_schemes"] = all_scheme_labels
            st.rerun()

    selected_sids: list[int] = []
    for lbl in scheme_sel or []:
        m = re.search(r"⟨id (\d+)⟩", lbl)
        if m:
            selected_sids.append(int(m.group(1)))

    can_queue = ADMIN and chosen_holders and perm_sel and selected_sids
    op_count = len(chosen_holders) * len(perm_sel or []) * len(selected_sids or [])
    st.markdown(
        f"<div class='jp-card'><b>Plan:</b> "
        f"{len(chosen_holders)} holder(s) × {len(perm_sel or [])} permission(s) × "
        f"{len(selected_sids or [])} scheme(s) = "
        f"<span class='jp-pill jp-grant'>{op_count} grant(s)</span> queued</div>",
        unsafe_allow_html=True,
    )

    skip_existing = st.checkbox(
        "Skip grants that already exist", value=True, key="grant_skip_existing"
    )
    if st.button("➕ Queue grants for preview", type="primary", disabled=not can_queue):
        existing_keysets: dict[int, set[tuple[str, str, str]]] = {}
        if skip_existing:
            for sid in selected_sids:
                det = fetch_scheme_detail(sid)
                existing_keysets[sid] = {
                    (g.permission_key, g.holder_type, g.holder_param)
                    for g in _parse_grants(det)
                }
        queued = 0
        skipped = 0
        already = 0
        for h in chosen_holders:
            for sid in selected_sids:
                sname = schemes_by_id.get(sid, {}).get("name", str(sid))
                for pkey in perm_sel:
                    if skip_existing and (pkey, h["type"], h["param"]) in existing_keysets.get(sid, set()):
                        skipped += 1
                        continue
                    op = PendingOp(
                        action="grant", scheme_id=sid, scheme_name=sname,
                        permission_key=pkey, holder_type=h["type"],
                        holder_param=h["param"], holder_display=h["display"],
                    )
                    if _queue(op):
                        queued += 1
                    else:
                        already += 1
        msg = f"Queued **{queued}** new op(s)."
        if skipped:
            msg += f" Skipped {skipped} already present."
        if already:
            msg += f" {already} were already in draft."
        st.success(msg)


# ===========================================================================
# Tab: Revoke — scan one holder across schemes and tick to revoke
# ===========================================================================
with tab_revoke:
    st.markdown("##### Revoke permissions in bulk")
    if not ADMIN:
        st.info("Read-only — admin role required to queue writes.")

    h = holder_picker("revoke_single", label="Holder to revoke from")
    if h:
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
                f"<div class='jp-empty'>No grants found for <b>{h['display']}</b>.</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"Found <span class='jp-pill jp-info'>{len(matching)}</span> grant(s) for "
                f"<b>{h['display']}</b> across "
                f"<span class='jp-pill jp-info'>{len({g.scheme_id for g in matching})}</span> scheme(s).",
                unsafe_allow_html=True,
            )
            by_scheme: dict[int, list[Grant]] = {}
            for g in matching:
                by_scheme.setdefault(g.scheme_id, []).append(g)
            sel_all = st.checkbox("Select all", key="revoke_sel_all")
            ticked: list[Grant] = []
            for sid in sorted(by_scheme.keys()):
                gs = by_scheme[sid]
                sname = gs[0].scheme_name
                st.markdown(f"**{sname}**  <span class='jp-pill'>id {sid}</span>", unsafe_allow_html=True)
                for g in gs:
                    chk = st.checkbox(
                        f"`{g.permission_key}` — {perm_name_by_key.get(g.permission_key, '')}",
                        value=sel_all,
                        key=f"revoke_chk_{sid}_{g.permission_id}",
                        disabled=not ADMIN,
                    )
                    if chk:
                        ticked.append(g)
            st.markdown("---")
            st.markdown(
                f"<div class='jp-card'><b>Plan:</b> revoke "
                f"<span class='jp-pill jp-revoke'>{len(ticked)} grant(s)</span> "
                f"from <b>{h['display']}</b></div>",
                unsafe_allow_html=True,
            )
            if st.button("➖ Queue revokes for preview", type="primary", disabled=not (ADMIN and ticked)):
                q = 0
                for g in ticked:
                    if _queue(PendingOp(
                        action="revoke", scheme_id=g.scheme_id,
                        scheme_name=g.scheme_name, permission_key=g.permission_key,
                        holder_type=g.holder_type, holder_param=g.holder_param,
                        holder_display=g.holder_display, permission_id=g.permission_id,
                    )):
                        q += 1
                st.success(f"Queued {q} revoke op(s).")


# ===========================================================================
# Tab: Copy / Move
# ===========================================================================
with tab_copy:
    st.markdown("##### Copy or move a holder's grants")
    if not ADMIN:
        st.info("Read-only — admin role required to queue writes.")
    cA, cB = st.columns(2)
    with cA:
        st.markdown("**Source (A)**")
        h_src = holder_picker("copy_src", label="Copy FROM")
    with cB:
        st.markdown("**Destination (B)**")
        h_dst = holder_picker("copy_dst", label="Copy TO")

    move = st.checkbox("Also revoke from source (move)", value=False, key="copy_move", disabled=not ADMIN)
    skip_existing = st.checkbox("Skip dest grants that already exist", value=True, key="copy_skip")

    if h_src and h_dst:
        if (h_src["type"], h_src["param"]) == (h_dst["type"], h_dst["param"]):
            st.warning("Source and destination are identical.")
        else:
            with st.spinner("Scanning source's grants…"):
                src_grants: list[Grant] = []
                for s in schemes:
                    det = fetch_scheme_detail(int(s["id"]))
                    for g in _parse_grants(det):
                        if g.matches_holder(h_src["type"], h_src["param"]):
                            src_grants.append(g)
            if not src_grants:
                st.markdown(
                    f"<div class='jp-empty'>Source <b>{h_src['display']}</b> has no grants.</div>",
                    unsafe_allow_html=True,
                )
            else:
                dest_keysets: dict[int, set[tuple[str, str, str]]] = {}
                if skip_existing:
                    for s in {g.scheme_id for g in src_grants}:
                        det = fetch_scheme_detail(s)
                        dest_keysets[s] = {
                            (g.permission_key, g.holder_type, g.holder_param)
                            for g in _parse_grants(det)
                        }
                planned = sum(
                    1 for g in src_grants
                    if not (skip_existing and (g.permission_key, h_dst["type"], h_dst["param"]) in dest_keysets.get(g.scheme_id, set()))
                )
                st.markdown(
                    f"<div class='jp-card'><b>Plan:</b> grant "
                    f"<span class='jp-pill jp-grant'>{planned}</span> to <b>{h_dst['display']}</b>"
                    + (f" · revoke <span class='jp-pill jp-revoke'>{len(src_grants)}</span> from <b>{h_src['display']}</b>" if move else "")
                    + f" · spans <span class='jp-pill jp-info'>{len({g.scheme_id for g in src_grants})}</span> scheme(s)"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button("⇄ Queue copy/move for preview", type="primary", disabled=not ADMIN):
                    queued = 0
                    for g in src_grants:
                        if not (skip_existing and (g.permission_key, h_dst["type"], h_dst["param"]) in dest_keysets.get(g.scheme_id, set())):
                            if _queue(PendingOp(
                                action="grant", scheme_id=g.scheme_id, scheme_name=g.scheme_name,
                                permission_key=g.permission_key, holder_type=h_dst["type"],
                                holder_param=h_dst["param"], holder_display=h_dst["display"],
                            )):
                                queued += 1
                        if move:
                            if _queue(PendingOp(
                                action="revoke", scheme_id=g.scheme_id, scheme_name=g.scheme_name,
                                permission_key=g.permission_key, holder_type=g.holder_type,
                                holder_param=g.holder_param, holder_display=g.holder_display,
                                permission_id=g.permission_id,
                            )):
                                queued += 1
                    st.success(f"Queued {queued} op(s).")


# ===========================================================================
# Tab: Locate
# ===========================================================================
with tab_search:
    st.markdown("##### Locate a holder across every scheme")
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
            c1, c2 = st.columns(2)
            c1.metric("Schemes touched", len(by_scheme))
            c2.metric("Total grants", len(hits))
            for sid in sorted(by_scheme.keys()):
                gs = by_scheme[sid]
                with st.expander(f"{gs[0].scheme_name}  —  {len(gs)} grant(s)", expanded=False):
                    for g in sorted(gs, key=lambda x: x.permission_key):
                        st.markdown(f"- `{g.permission_key}` — {perm_name_by_key.get(g.permission_key, g.permission_key)}")


# ===========================================================================
# Tab: Approvals — pending + history. The execution path for every Jira
# write originates here.
# ===========================================================================
with tab_approvals:
    st.markdown("##### Approval queue")
    if not _schema_ok:
        st.markdown(
            "<div class='jp-empty'>Postgres unavailable — approvals can't be persisted.</div>",
            unsafe_allow_html=True,
        )
    else:
        c1, c2 = st.columns([1, 3])
        with c1:
            view_mode = st.radio(
                "View",
                options=["pending", "all", "history"],
                format_func=lambda x: {"pending": "Pending", "all": "All", "history": "History only"}[x],
                horizontal=True,
                key="approvals_view_mode",
            )
        statuses = None
        if view_mode == "pending":
            statuses = ["pending"]
        elif view_mode == "history":
            statuses = ["approved", "rejected", "executed", "partial", "failed", "cancelled"]

        approvals, aerr = db_list_approvals(statuses=statuses, limit=200)
        if aerr:
            st.error(aerr)
        elif not approvals:
            st.markdown(
                "<div class='jp-empty'>Nothing here yet — submit a draft from the preview pane.</div>",
                unsafe_allow_html=True,
            )

        for a in approvals:
            klass = {
                "pending":   "",
                "approved":  "jp-st-approved",
                "rejected":  "jp-st-rejected",
                "executed":  "jp-st-executed",
                "partial":   "jp-st-partial",
                "failed":    "jp-st-failed",
                "cancelled": "jp-st-rejected",
            }.get(a["status"], "")
            ts_req = a["ts_requested"].strftime("%Y-%m-%d %H:%M") if hasattr(a["ts_requested"], "strftime") else str(a["ts_requested"])
            head = (
                f"<div class='jp-approval {klass}'>"
                f"<div style='display:flex;justify-content:space-between;gap:1rem;'>"
                f"<div>"
                f"<b>#{a['id']}</b>  "
                f"<span class='jp-pill {'jp-grant' if a['status']=='executed' else ('jp-warn' if a['status']=='pending' else ('jp-revoke' if a['status'] in ('rejected','failed') else 'jp-info'))}'>{a['status']}</span>  "
                f"<span class='jp-pill'>{a['mode']}</span>  "
                f"<span style='color:var(--jp-text-dim);'>by <b>{a['requester']}</b> · {ts_req}</span>"
                f"</div>"
                f"<div style='color:var(--jp-text-mute);font-size:.78rem;'>"
                f"+{a['grant_count']} grants / −{a['revoke_count']} revokes · {a['schemes_touched']} scheme(s)"
                f"</div></div>"
            )
            if a.get("reason"):
                head += f"<div style='margin-top:.4rem;font-size:.85rem;color:var(--jp-text-dim);'>📝 {a['reason']}</div>"
            if a.get("approver"):
                ts_dec = a["ts_decided"].strftime("%Y-%m-%d %H:%M") if hasattr(a["ts_decided"], "strftime") else str(a.get("ts_decided") or "")
                head += f"<div style='margin-top:.3rem;font-size:.78rem;color:var(--jp-text-mute);'>Decided by <b>{a['approver']}</b> at {ts_dec}</div>"
            if a.get("decision_note"):
                head += f"<div style='font-size:.78rem;color:var(--jp-text-mute);'>💬 {a['decision_note']}</div>"
            head += "</div>"
            st.markdown(head, unsafe_allow_html=True)

            if a["status"] == "pending" and ADMIN:
                with st.expander(f"Inspect & decide on request #{a['id']}", expanded=False):
                    full, lerr = db_load_approval(int(a["id"]))
                    if lerr:
                        st.error(lerr)
                    elif not full:
                        st.error("Lookup failed.")
                    else:
                        ops = full.get("ops") or []
                        grants_in = [o for o in ops if o.get("action") == "grant"]
                        revokes_in = [o for o in ops if o.get("action") == "revoke"]
                        cc1, cc2 = st.columns(2)
                        cc1.markdown(f"**Grants ({len(grants_in)})**")
                        for o in grants_in[:200]:
                            cc1.markdown(
                                f"<div class='jp-grant-row jp-add'>"
                                f"<span class='jp-perm'>+ {o['permission_key']}</span>"
                                f"<span class='jp-holder'>{o['holder_type']} <b>{o['holder_display']}</b> ⟨{o['holder_param']}⟩ · <i>{o['scheme_name']}</i></span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                        if len(grants_in) > 200:
                            cc1.caption(f"…and {len(grants_in) - 200} more (truncated)")
                        cc2.markdown(f"**Revokes ({len(revokes_in)})**")
                        for o in revokes_in[:200]:
                            cc2.markdown(
                                f"<div class='jp-grant-row jp-del'>"
                                f"<span class='jp-perm'>− {o['permission_key']}</span>"
                                f"<span class='jp-holder'>{o['holder_type']} <b>{o['holder_display']}</b> ⟨{o['holder_param']}⟩ · <i>{o['scheme_name']}</i></span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                        if len(revokes_in) > 200:
                            cc2.caption(f"…and {len(revokes_in) - 200} more (truncated)")

                        # Decision controls
                        is_self_request = (a["requester"] == ACTOR)
                        two_person = (a["mode"] == "two-person")
                        block_self_approve = two_person and is_self_request
                        if block_self_approve:
                            st.warning(
                                "Two-person mode: you can't approve your own request. "
                                "Another admin must decide."
                            )
                        note = st.text_input("Decision note (optional)", key=f"appr_note_{a['id']}")
                        d1, d2, d3 = st.columns(3)
                        approve_clicked = d1.button(
                            "✅ Approve & execute", type="primary",
                            key=f"appr_yes_{a['id']}",
                            disabled=not ADMIN or block_self_approve,
                        )
                        reject_clicked = d2.button(
                            "❌ Reject", key=f"appr_no_{a['id']}",
                            disabled=not ADMIN or block_self_approve,
                        )
                        cancel_clicked = False
                        if is_self_request:
                            cancel_clicked = d3.button(
                                "🗑 Withdraw (requester)", key=f"appr_cancel_{a['id']}",
                                disabled=not ADMIN,
                            )

                        if approve_clicked:
                            ok, err = db_decide_approval(
                                int(a["id"]), approver=ACTOR, decision="approved", note=note,
                            )
                            if not ok:
                                st.error(err or "Approval failed.")
                            else:
                                # Re-load post-decision (so exec_summary etc. references the live row)
                                full2, _ = db_load_approval(int(a["id"]))
                                with st.spinner(f"Applying {full2.get('op_count', 0)} op(s) to Jira…"):
                                    okc, fc = _execute_approved_request(full2)
                                if fc == 0:
                                    st.success(f"All {okc} op(s) applied.")
                                else:
                                    st.warning(f"{okc} ok, {fc} failed. See Audit tab.")
                                st.rerun()
                        if reject_clicked:
                            ok, err = db_decide_approval(
                                int(a["id"]), approver=ACTOR, decision="rejected", note=note,
                            )
                            if ok:
                                st.success("Rejected.")
                                st.rerun()
                            else:
                                st.error(err)
                        if cancel_clicked:
                            ok, err = db_decide_approval(
                                int(a["id"]), approver=ACTOR, decision="cancelled", note=note or "withdrawn by requester",
                            )
                            if ok:
                                st.success("Withdrawn.")
                                st.rerun()
                            else:
                                st.error(err)
            elif a["status"] in ("executed", "partial", "failed") and a.get("exec_summary"):
                summary = a["exec_summary"]
                if isinstance(summary, (str, bytes, bytearray)):
                    try:
                        summary = json.loads(summary)
                    except Exception:
                        summary = {}
                if isinstance(summary, dict):
                    st.caption(
                        f"Exec result: ✓ {summary.get('ok', 0)} ok · ✗ {summary.get('fail', 0)} fail · "
                        f"{summary.get('audit_rows', 0)} audit row(s) recorded"
                    )


# ===========================================================================
# Tab: Audit — query Postgres, filter, export
# ===========================================================================
with tab_audit:
    st.markdown("##### Audit log")
    if not _schema_ok:
        st.markdown(
            "<div class='jp-empty'>Postgres unavailable — audit log can't be read.</div>",
            unsafe_allow_html=True,
        )
    else:
        st.caption(
            "Every Jira write that ran through this page. Sourced from "
            "`jira_perm_audit` in Postgres. Filter by actor, holder, scheme, "
            "date, or free text. CSV / JSON export for permanent record."
        )
        f1, f2, f3, f4 = st.columns(4)
        with f1:
            f_since = st.date_input(
                "Since", value=(datetime.now(timezone.utc) - timedelta(days=14)).date(),
                key="audit_since",
            )
        with f2:
            f_until = st.date_input(
                "Until", value=datetime.now(timezone.utc).date(),
                key="audit_until",
            )
        with f3:
            f_actor = st.text_input("Actor", key="audit_actor")
        with f4:
            f_action = st.selectbox("Action", ["", "grant", "revoke"], key="audit_action")
        f5, f6, f7 = st.columns(3)
        with f5:
            f_holder = st.text_input("Holder (substring)", key="audit_holder")
        with f6:
            scheme_map_audit = {0: "— Any scheme —", **{int(s["id"]): s["name"] for s in schemes}}
            f_scheme = st.selectbox(
                "Scheme", list(scheme_map_audit.keys()),
                format_func=lambda k: scheme_map_audit[k], key="audit_scheme",
            )
        with f7:
            f_ok = st.selectbox(
                "Status", ["any", "ok", "err"],
                format_func=lambda x: {"any":"All","ok":"Successful only","err":"Errors only"}[x],
                key="audit_okfilter",
            )
        f_text = st.text_input("Free-text (matches error, permission, scheme name)", key="audit_text")
        limit = st.slider("Result limit", 50, 5000, 500, step=50, key="audit_limit")

        since_dt = datetime.combine(f_since, datetime.min.time(), tzinfo=timezone.utc) if f_since else None
        until_dt = datetime.combine(f_until, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc) if f_until else None
        rows, qerr = db_audit_query(
            since=since_dt, until=until_dt,
            actor=f_actor, action=f_action,
            scheme_id=f_scheme if f_scheme else None,
            holder=f_holder, ok=f_ok, text=f_text, limit=limit,
        )
        if qerr:
            st.error(qerr)
        else:
            st.markdown(f"Showing **{len(rows)}** entries.")
            if not rows:
                st.markdown(
                    "<div class='jp-empty'>No audit rows match the filter.</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    "<div class='jp-audit-row' style='font-weight:600;color:var(--jp-text-mute);'>"
                    "<div>Timestamp (UTC)</div><div>Action</div><div>Status</div>"
                    "<div>Detail</div><div>Scheme</div></div>",
                    unsafe_allow_html=True,
                )
                for r in rows:
                    ts = r["ts"].strftime("%Y-%m-%d %H:%M:%S") if hasattr(r["ts"], "strftime") else str(r["ts"])
                    action = r["action"]
                    pill = f"<span class='jp-pill jp-{'grant' if action=='grant' else 'revoke'}'>{action}</span>"
                    if r["ok"]:
                        status_html = "<span class='jp-status-ok'>✓ ok</span>"
                    else:
                        status_html = f"<span class='jp-status-err'>✗ {r.get('status_code') or 'err'}</span>"
                    detail = (
                        f"<code>{r['permission_key']}</code> · "
                        f"{r['holder_type']} <b>{r.get('holder_display') or r['holder_param']}</b> ⟨{r['holder_param']}⟩ "
                        f"<span style='color:var(--jp-text-mute);'>(by {r['actor']}"
                        + (f", req #{r['approval_id']}" if r.get('approval_id') else "")
                        + ")</span>"
                    )
                    if not r["ok"] and r.get("error"):
                        detail += f"<br><span style='color:var(--jp-red);font-size:.75rem;'>{str(r['error'])[:200]}</span>"
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

                st.markdown("---")
                # Export
                cE1, cE2 = st.columns(2)
                cE1.download_button(
                    "⬇ Export JSON",
                    data=json.dumps(rows, default=str, indent=2),
                    file_name=f"jira-perm-audit-{int(time.time())}.json",
                    mime="application/json",
                    use_container_width=True,
                )
                buf = io.StringIO()
                fieldnames = list(rows[0].keys())
                w = csv.DictWriter(buf, fieldnames=fieldnames)
                w.writeheader()
                for r in rows:
                    w.writerow({k: ("" if v is None else (json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v))) for k, v in r.items()})
                cE2.download_button(
                    "⬇ Export CSV",
                    data=buf.getvalue(),
                    file_name=f"jira-perm-audit-{int(time.time())}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )


# ===========================================================================
# Pending preview pane — sticky at the bottom whenever there's a draft.
# Submits to the approval queue (DB) — no direct Jira call from here.
# ===========================================================================
pending = st.session_state["jp_pending"]
if pending:
    st.markdown("---")
    st.markdown("## 🔍 Preview & submit pending draft")

    grants_p = [p for p in pending if p["action"] == "grant"]
    revokes_p = [p for p in pending if p["action"] == "revoke"]

    c1, c2, c3 = st.columns([1, 1, 2])
    c1.markdown(
        f"<div class='jp-card'><div class='jp-diff-num jp-add'>+{len(grants_p)}</div>"
        f"<div style='color:var(--jp-text-mute);font-size:.8rem;'>grants to add</div></div>",
        unsafe_allow_html=True,
    )
    c2.markdown(
        f"<div class='jp-card'><div class='jp-diff-num jp-del'>-{len(revokes_p)}</div>"
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

    st.markdown("---")
    mode = st.session_state["jp_approval_mode"]
    reason = st.text_input(
        "Justification (recorded with the request) — required",
        key="submit_reason",
        placeholder="e.g. onboarding 5 devs to the Payments project",
    )

    if mode == "self":
        st.markdown(
            f"<span class='jp-pill jp-warn'>Self-approve mode</span> "
            f"&nbsp;Submitting will record a request, auto-approve as <b>{ACTOR}</b>, "
            f"then execute immediately.",
            unsafe_allow_html=True,
        )
        confirm_text = f"APPLY {len(pending)}"
        typed = st.text_input(
            f"Type **{confirm_text}** to enable submit:",
            key="apply_confirm_self",
            disabled=not ADMIN,
        )
        ready = ADMIN and reason.strip() and typed.strip() == confirm_text
        cA, cB = st.columns(2)
        submit = cA.button(
            f"🚀 Submit & execute {len(pending)} op(s)", type="primary",
            disabled=(not ready) or (not _schema_ok),
        )
        if cB.button("Discard draft", key="discard_self"):
            _clear_pending()
            st.rerun()
        if submit:
            aid, err = db_create_approval_request(
                requester=ACTOR, mode="self", ops=pending, reason=reason.strip(),
            )
            if err or not aid:
                st.error(err or "Could not create approval request.")
            else:
                ok, err2 = db_decide_approval(
                    int(aid), approver=ACTOR, decision="approved",
                    note="self-approved at submit",
                )
                if not ok:
                    st.error(err2 or "Self-approve failed.")
                else:
                    full, _ = db_load_approval(int(aid))
                    with st.spinner(f"Executing {full.get('op_count', 0)} op(s)…"):
                        okc, fc = _execute_approved_request(full)
                    if fc == 0:
                        st.success(f"Request #{aid}: all {okc} op(s) applied.")
                    else:
                        st.warning(f"Request #{aid}: {okc} ok, {fc} failed — see Audit tab.")
                    _clear_pending()
                    st.session_state["jp_last_submit_id"] = aid

    else:  # two-person
        st.markdown(
            f"<span class='jp-pill jp-info'>Two-person mode</span> "
            f"&nbsp;Submitting will queue a request for a *different* admin to approve. "
            f"Nothing reaches Jira until they decide.",
            unsafe_allow_html=True,
        )
        ready = ADMIN and reason.strip() and _schema_ok
        cA, cB = st.columns(2)
        submit = cA.button(
            f"📨 Submit {len(pending)} op(s) for approval", type="primary",
            disabled=not ready,
        )
        if cB.button("Discard draft", key="discard_two"):
            _clear_pending()
            st.rerun()
        if submit:
            aid, err = db_create_approval_request(
                requester=ACTOR, mode="two-person", ops=pending, reason=reason.strip(),
            )
            if err or not aid:
                st.error(err or "Submission failed.")
            else:
                st.success(f"Request #{aid} queued. Awaiting approval in the **🔐 Approvals** tab.")
                _clear_pending()
                st.session_state["jp_last_submit_id"] = aid
else:
    st.markdown(
        "<div style='margin-top:1.5rem;text-align:center;color:var(--jp-text-mute);font-size:.85rem;'>"
        "No pending draft — queue grants or revokes from the tabs above to preview them here."
        "</div>",
        unsafe_allow_html=True,
    )
