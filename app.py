"""
Ollama Document Chatbot — Streamlit Page
Chat with your documents using local Ollama models.
Supports TXT, MD, PDF, and DOCX files.
Queue-based access: one user at a time.
Designed as a page within a multi-page Streamlit app.
"""

import json
import os
import re
import subprocess
import tempfile
import time
import hashlib
import threading
from datetime import datetime, timedelta
from pathlib import Path
from io import BytesIO
from typing import Optional

import requests
import streamlit as st

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
except ImportError:
    Workbook = None

# ---------------------------------------------------------------------------
# Optional heavy imports
# ---------------------------------------------------------------------------
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None

try:
    import olefile
except ImportError:
    olefile = None

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None


# ---------------------------------------------------------------------------
# Configuration — set these once
# ---------------------------------------------------------------------------
OLLAMA_URL = "http://ef-nexus-03:8081"
MODEL = "qwen3.5:9b"
HISTORY_SCHEMA = "public"           # postgres schema
HISTORY_TABLE  = "chatbot_history"  # postgres table name

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
QUEUE_FILE = Path(__file__).parent / ".queue_lock.json"
LOCK = threading.Lock()
SESSION_TIMEOUT_SECONDS = 300
CHARS_PER_TOKEN_ESTIMATE = 4  # rough char-to-token ratio


# ---------------------------------------------------------------------------
# Page-scoped CSS
# ---------------------------------------------------------------------------
CUSTOM_CSS = """
<style>
:root {
    --bg-primary: #f5f2eb;
    --bg-secondary: #eae6dd;
    --bg-card: #ffffff;
    --accent: #4a90d9;
    --accent-hover: #3a7bc8;
    --accent-subtle: rgba(74, 144, 217, 0.08);
    --text-primary: #1a1a2e;
    --text-secondary: #6b7280;
    --text-muted: #9ca3af;
    --border: #d5d0c4;
    --border-light: #e8e4db;
    --success: #2d8a4e;
    --warning: #b8860b;
    --danger: #c0392b;
    --radius: 12px;
    --radius-sm: 8px;
}

/* ---------- Page background ---------- */
.stApp, section[data-testid="stMain"] {
    background-color: var(--bg-primary) !important;
}

.stChatMessage {
    border-radius: var(--radius) !important;
    border: 1px solid var(--border-light) !important;
    background: var(--bg-card) !important;
    margin-bottom: 0.6rem !important;
    padding: 1rem 1.25rem !important;
    position: relative;
}

.stButton > button {
    border-radius: var(--radius-sm) !important;
    font-weight: 500 !important;
    font-size: 0.85rem !important;
    transition: all 0.15s ease !important;
    border: 1px solid var(--border) !important;
    background: var(--bg-card) !important;
    color: var(--text-primary) !important;
}
.stButton > button:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    background: var(--accent-subtle) !important;
}

.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 500;
    white-space: nowrap;
    vertical-align: middle;
}
.status-active {
    background: rgba(45, 138, 78, 0.1);
    color: var(--success);
    border: 1px solid rgba(45, 138, 78, 0.25);
}
.status-busy {
    background: rgba(184, 134, 11, 0.1);
    color: var(--warning);
    border: 1px solid rgba(184, 134, 11, 0.25);
}
.status-offline {
    background: rgba(192, 57, 43, 0.1);
    color: var(--danger);
    border: 1px solid rgba(192, 57, 43, 0.25);
}

.doc-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 5px 11px;
    margin: 2px;
    font-size: 0.8rem;
    color: var(--text-primary);
}

.panel-section-title {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-secondary);
    margin-bottom: 0.35rem;
}

.divider {
    border: none;
    border-top: 1px solid var(--border-light);
    margin: 0.75rem 0;
}

.toolbar-brand {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.02em;
    white-space: nowrap;
}
.toolbar-row {
    display: flex;
    align-items: center;
    gap: 10px;
}
.toolbar-meta {
    font-size: 0.78rem;
    color: var(--text-secondary);
    white-space: nowrap;
}

.empty-state {
    text-align: center;
    padding: 4rem 1rem 2rem;
    color: var(--text-secondary);
}
.empty-state h2 {
    font-size: 1.4rem;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 0.5rem;
}
.empty-state p {
    font-size: 0.92rem;
    max-width: 420px;
    margin: 0 auto;
    line-height: 1.6;
}

.lobby-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 2.5rem 2rem;
    text-align: center;
    max-width: 440px;
    margin: 3rem auto;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.lobby-card h2 {
    font-size: 1.35rem;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 0.4rem;
}
.lobby-card p {
    color: var(--text-secondary);
    font-size: 0.9rem;
    line-height: 1.5;
    margin-bottom: 1.2rem;
}

div[data-testid="stPopover"] > div {
    min-width: 320px !important;
}

.doc-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding: 0.5rem 0;
}
.doc-bar-label {
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-right: 4px;
}

/* ---------- Message metadata ---------- */
.msg-meta {
    display: flex;
    justify-content: flex-end;
    gap: 12px;
    margin-top: 0.4rem;
    font-size: 0.7rem;
    color: var(--text-muted);
    letter-spacing: 0.01em;
}
.msg-meta span {
    display: inline-flex;
    align-items: center;
    gap: 3px;
}

/* ---------- Document preview ---------- */
.doc-preview-card {
    background: var(--bg-primary);
    border: 1px solid var(--border-light);
    border-radius: var(--radius-sm);
    margin-top: 0.5rem;
}
.doc-preview-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid var(--border-light);
}
.doc-preview-name {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--text-primary);
}
.doc-preview-stats {
    display: flex;
    gap: 10px;
    font-size: 0.7rem;
    color: var(--text-muted);
}
.doc-preview-stats span {
    display: inline-flex;
    align-items: center;
    gap: 3px;
}
.doc-preview-body {
    padding: 0.6rem 0.75rem;
    font-size: 0.78rem;
    color: var(--text-secondary);
    line-height: 1.55;
    max-height: 160px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
}

/* ---------- History / Admin view ---------- */
.history-section {
    margin-top: 2.5rem;
    border-top: 2px solid var(--border);
    padding-top: 1.5rem;
}
.history-section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 1.25rem;
}
.history-section-title {
    display: flex;
    align-items: center;
    gap: 10px;
}
.history-section-title h3 {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0;
    letter-spacing: -0.02em;
}
.admin-badge {
    display: inline-flex;
    align-items: center;
    padding: 2px 9px;
    border-radius: 20px;
    font-size: 0.67rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    background: rgba(74, 144, 217, 0.1);
    color: var(--accent);
    border: 1px solid rgba(74, 144, 217, 0.3);
}
.stat-grid {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 0.75rem;
    margin-bottom: 0.75rem;
}
.stat-card {
    background: var(--bg-card);
    border: 1px solid var(--border-light);
    border-radius: var(--radius);
    padding: 1rem 1rem 0.85rem;
    text-align: center;
    position: relative;
    overflow: hidden;
}
.stat-card::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: var(--accent);
    opacity: 0.4;
    border-radius: var(--radius) var(--radius) 0 0;
}
.stat-card .stat-value {
    font-size: 1.55rem;
    font-weight: 700;
    color: var(--text-primary);
    line-height: 1.15;
}
.stat-card .stat-label {
    font-size: 0.7rem;
    font-weight: 500;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-top: 0.25rem;
}
.stat-card .stat-sub {
    font-size: 0.68rem;
    color: var(--text-muted);
    margin-top: 0.15rem;
}
.stat-meta-row {
    display: flex;
    gap: 20px;
    font-size: 0.75rem;
    color: var(--text-muted);
    padding: 0.4rem 0 1rem;
    flex-wrap: wrap;
}
.stat-meta-row span strong {
    color: var(--text-secondary);
    font-weight: 600;
}
.filter-bar {
    background: var(--bg-card);
    border: 1px solid var(--border-light);
    border-radius: var(--radius);
    padding: 0.85rem 1rem;
    margin-bottom: 1rem;
}
.filter-bar-label {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-secondary);
    margin-bottom: 0.5rem;
}
.session-card {
    background: var(--bg-card);
    border: 1px solid var(--border-light);
    border-radius: var(--radius);
    margin-bottom: 0.6rem;
    overflow: hidden;
}
.session-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.7rem 1rem;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border-light);
    flex-wrap: wrap;
    gap: 6px;
}
.session-card-id {
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.72rem;
    color: var(--text-muted);
    background: var(--bg-primary);
    border: 1px solid var(--border-light);
    border-radius: 4px;
    padding: 1px 6px;
}
.session-card-user {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--text-primary);
}
.session-card-meta {
    display: flex;
    gap: 12px;
    font-size: 0.72rem;
    color: var(--text-muted);
    flex-wrap: wrap;
}
.history-row {
    background: var(--bg-card);
    border: 1px solid var(--border-light);
    border-left: 3px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 0.75rem 1rem;
    margin-bottom: 0.35rem;
}
.history-row.role-user {
    border-left-color: var(--accent);
}
.history-row.role-assistant {
    border-left-color: var(--success);
}
.history-row-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.3rem;
    flex-wrap: wrap;
    gap: 4px;
}
.history-role-badge {
    display: inline-flex;
    align-items: center;
    padding: 1px 8px;
    border-radius: 10px;
    font-size: 0.68rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.history-role-badge.role-user {
    background: rgba(74, 144, 217, 0.1);
    color: var(--accent);
    border: 1px solid rgba(74, 144, 217, 0.2);
}
.history-role-badge.role-assistant {
    background: rgba(45, 138, 78, 0.1);
    color: var(--success);
    border: 1px solid rgba(45, 138, 78, 0.2);
}
.history-row-ident {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: 0.72rem;
    color: var(--text-muted);
}
.history-content {
    font-size: 0.84rem;
    color: var(--text-primary);
    line-height: 1.5;
    margin-bottom: 0.3rem;
    word-break: break-word;
}
.history-row-meta {
    font-size: 0.7rem;
    color: var(--text-muted);
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
}
.chart-card {
    background: var(--bg-card);
    border: 1px solid var(--border-light);
    border-radius: var(--radius);
    padding: 1rem 1.25rem 0.5rem;
    margin-bottom: 0.75rem;
}
.chart-card-title {
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-secondary);
    margin-bottom: 0.5rem;
}
.chart-section-divider {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 1.25rem 0 0.75rem;
}
.chart-section-divider span {
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-secondary);
    white-space: nowrap;
}
.chart-section-divider::after {
    content: "";
    flex: 1;
    height: 1px;
    background: var(--border-light);
}
.chart-card .stDataFrame {
    border: none !important;
}
.chart-card .stDataFrame [data-testid="stDataFrameResizable"] {
    border: 1px solid var(--border-light) !important;
    border-radius: var(--radius-sm) !important;
}
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def count_words(text: str) -> int:
    return len(text.split())


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def format_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"{mins}m {secs:.0f}s"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
_db_config_cache: Optional[dict] = None


def _get_db_config() -> Optional[dict]:
    """Load DB config via VaultClient. Cached after first successful call."""
    global _db_config_cache
    if _db_config_cache is not None:
        return _db_config_cache
    try:
        from utils.vault import VaultClient
        vc = VaultClient()
        _db_config_cache = vc.read_all_nested_secrets("postgres")
        return _db_config_cache
    except Exception:
        return None


def _get_conn():
    """Create a fresh psycopg2 connection with autocommit. Caller must close."""
    if psycopg2 is None:
        return None
    config = _get_db_config()
    if not config:
        return None
    try:
        conn = psycopg2.connect(
            host=config["host"],
            port=config["port"],
            dbname=config["database"],
            user=config["username"],
            password=config["password"],
            connect_timeout=5,
        )
        conn.autocommit = True
        return conn
    except Exception as e:
        import sys
        print(f"[DB CONNECT] ERROR: {e}", file=sys.stderr)
        return None


def db_ensure_table():
    """Create the history table if it doesn't exist, and migrate missing columns."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {HISTORY_SCHEMA}.{HISTORY_TABLE} (
                    id              BIGSERIAL PRIMARY KEY,
                    session_id      TEXT        NOT NULL,
                    username        TEXT,
                    role            TEXT        NOT NULL,
                    content         TEXT        NOT NULL,
                    timestamp_utc   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    duration_s      NUMERIC,
                    tokens_est      INTEGER,
                    model           TEXT,
                    documents       TEXT[]
                )
            """)
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{HISTORY_TABLE}_session
                    ON {HISTORY_SCHEMA}.{HISTORY_TABLE} (session_id, timestamp_utc)
            """)
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{HISTORY_TABLE}_username
                    ON {HISTORY_SCHEMA}.{HISTORY_TABLE} (username)
            """)
            cur.execute(f"""
                ALTER TABLE {HISTORY_SCHEMA}.{HISTORY_TABLE}
                    ADD COLUMN IF NOT EXISTS username TEXT
            """)
    except Exception as e:
        import sys
        print(f"[db_ensure_table] ERROR: {e}", file=sys.stderr)
    finally:
        if conn:
            conn.close()


def db_save_message(msg: dict, session_id: str, username: str, documents: list[str]):
    """Persist a single message to postgres."""
    conn = _get_conn()
    if conn is None:
        st.toast("DB: could not connect", icon="⚠️")
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {HISTORY_SCHEMA}.{HISTORY_TABLE}
                    (session_id, username, role, content, timestamp_utc,
                     duration_s, tokens_est, model, documents)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    username or "",
                    msg["role"],
                    msg["content"],
                    datetime.utcnow(),
                    msg.get("duration"),
                    msg.get("tokens"),
                    MODEL,
                    documents or [],
                ),
            )
    except Exception as e:
        import sys
        print(f"[db_save_message] ERROR: {e}", file=sys.stderr)
        st.toast(f"DB save error: {e}", icon="⚠️")
    finally:
        conn.close()


def db_fetch_history(
    limit: int = 200,
    session_filter: Optional[str] = None,
    username_filter: Optional[str] = None,
    role_filter: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list[dict]:
    """Fetch conversation history rows for the admin view."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        clauses = []
        params: list = []
        if session_filter:
            clauses.append("session_id = %s")
            params.append(session_filter)
        if username_filter:
            clauses.append("username = %s")
            params.append(username_filter)
        if role_filter and role_filter != "All":
            clauses.append("role = %s")
            params.append(role_filter.lower())
        if date_from:
            clauses.append("timestamp_utc >= %s")
            params.append(date_from)
        if date_to:
            clauses.append("timestamp_utc <= %s")
            params.append(date_to)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT id, session_id, username, role, content, timestamp_utc,
                       duration_s, tokens_est, model, documents
                FROM {HISTORY_SCHEMA}.{HISTORY_TABLE}
                {where}
                ORDER BY timestamp_utc DESC
                LIMIT %s
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def db_fetch_stats() -> dict:
    """Aggregate stats for the admin dashboard."""
    conn = _get_conn()
    if conn is None:
        return {}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT
                    COUNT(*)                                        AS total_messages,
                    COUNT(DISTINCT session_id)                      AS total_sessions,
                    COUNT(*) FILTER (WHERE role = 'user')          AS user_messages,
                    COUNT(*) FILTER (WHERE role = 'assistant')     AS assistant_messages,
                    COALESCE(SUM(tokens_est), 0)                   AS total_tokens,
                    COALESCE(AVG(duration_s)
                        FILTER (WHERE role = 'assistant'), 0)      AS avg_duration_s,
                    COALESCE(MAX(duration_s)
                        FILTER (WHERE role = 'assistant'), 0)      AS max_duration_s,
                    MIN(timestamp_utc)                             AS first_message,
                    MAX(timestamp_utc)                             AS last_message
                FROM {HISTORY_SCHEMA}.{HISTORY_TABLE}
            """)
            return dict(cur.fetchone() or {})
    except Exception:
        return {}
    finally:
        if conn:
            conn.close()


def db_fetch_sessions() -> list[dict]:
    """List all distinct sessions with summary."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT
                    session_id,
                    MAX(username)       AS username,
                    MIN(timestamp_utc)  AS started_at,
                    MAX(timestamp_utc)  AS last_at,
                    COUNT(*)            AS message_count,
                    SUM(tokens_est)     AS total_tokens,
                    AVG(duration_s)
                        FILTER (WHERE role = 'assistant') AS avg_duration_s
                FROM {HISTORY_SCHEMA}.{HISTORY_TABLE}
                GROUP BY session_id
                ORDER BY last_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def db_fetch_usernames() -> list[str]:
    """Return all distinct usernames for the filter dropdown."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT DISTINCT username FROM {HISTORY_SCHEMA}.{HISTORY_TABLE}
                WHERE username IS NOT NULL AND username != ''
                ORDER BY username
            """)
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def db_clear_all() -> bool:
    """Delete all rows from the history table. Returns True on success."""
    conn = _get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {HISTORY_SCHEMA}.{HISTORY_TABLE}")
        return True
    except Exception:
        return False
    finally:
        if conn:
            conn.close()


def db_fetch_timeseries() -> list[dict]:
    """Daily aggregates for charts: messages, sessions, tokens, avg response."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT
                    DATE(timestamp_utc)                                          AS day,
                    COUNT(*)                                                     AS messages,
                    COUNT(DISTINCT session_id)                                   AS sessions,
                    COUNT(*) FILTER (WHERE role = 'user')                       AS user_msgs,
                    COUNT(*) FILTER (WHERE role = 'assistant')                  AS assistant_msgs,
                    COALESCE(SUM(tokens_est), 0)                                AS tokens,
                    COALESCE(AVG(duration_s) FILTER (WHERE role='assistant'), 0) AS avg_duration
                FROM {HISTORY_SCHEMA}.{HISTORY_TABLE}
                GROUP BY DATE(timestamp_utc)
                ORDER BY day
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def db_fetch_user_activity() -> list[dict]:
    """Per-user aggregate stats for the user activity chart."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT
                    COALESCE(NULLIF(username, ''), '(anonymous)') AS username,
                    COUNT(*)                                      AS total_messages,
                    COUNT(DISTINCT session_id)                    AS sessions,
                    COUNT(*) FILTER (WHERE role = 'user')        AS user_msgs,
                    COUNT(*) FILTER (WHERE role = 'assistant')   AS assistant_msgs,
                    COALESCE(SUM(tokens_est), 0)                 AS tokens,
                    MIN(timestamp_utc)                            AS first_active,
                    MAX(timestamp_utc)                            AS last_active
                FROM {HISTORY_SCHEMA}.{HISTORY_TABLE}
                GROUP BY COALESCE(NULLIF(username, ''), '(anonymous)')
                ORDER BY total_messages DESC
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def db_fetch_session_topics(limit: int = 50) -> list[dict]:
    """First user message per session — used as a topic proxy."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT DISTINCT ON (session_id)
                    session_id,
                    COALESCE(NULLIF(username, ''), '(anonymous)') AS username,
                    content,
                    timestamp_utc
                FROM {HISTORY_SCHEMA}.{HISTORY_TABLE}
                WHERE role = 'user'
                ORDER BY session_id, timestamp_utc ASC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def _extract_topic_keywords(topics: list[dict], top_n: int = 20) -> list[tuple[str, int]]:
    """Simple keyword frequency from session-opening user messages."""
    STOP = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "shall",
        "should", "may", "might", "must", "can", "could", "i", "me", "my",
        "you", "your", "we", "our", "they", "them", "their", "he", "she",
        "it", "its", "this", "that", "these", "those", "what", "which",
        "who", "whom", "how", "when", "where", "why", "not", "no", "nor",
        "and", "or", "but", "if", "then", "so", "as", "of", "in", "on",
        "at", "to", "for", "with", "by", "from", "about", "into", "through",
        "during", "before", "after", "above", "below", "up", "down", "out",
        "off", "over", "under", "again", "further", "once", "here", "there",
        "all", "each", "every", "both", "few", "more", "most", "other",
        "some", "such", "only", "own", "same", "than", "too", "very",
        "just", "because", "also", "any", "many", "much", "like", "get",
        "got", "make", "know", "think", "want", "tell", "see", "go", "come",
        "take", "give", "use", "find", "say", "said", "let", "need", "try",
        "ask", "work", "call", "put", "keep", "still", "should", "could",
        "hi", "hello", "please", "thanks", "thank", "hey", "ok", "okay",
    }
    freq: dict[str, int] = {}
    for t in topics:
        words = re.findall(r"[a-zA-Z]{3,}", t.get("content", "").lower())
        for w in words:
            if w not in STOP:
                freq[w] = freq.get(w, 0) + 1
    return sorted(freq.items(), key=lambda x: x[1], reverse=True)[:top_n]


# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
def _init_state():
    defaults = {
        "chat_session_id": "",
        "chat_messages": [],  # each: {role, content, timestamp, duration?, tokens?}
        "documents": {},      # name -> {content, word_count, token_count}
        "chat_active": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()
db_ensure_table()


# ---------------------------------------------------------------------------
# Queue / lock helpers
# ---------------------------------------------------------------------------
def _read_lock() -> Optional[dict]:
    if QUEUE_FILE.exists():
        try:
            data = json.loads(QUEUE_FILE.read_text())
            last_active = datetime.fromisoformat(data.get("last_active", ""))
            if datetime.now() - last_active > timedelta(seconds=SESSION_TIMEOUT_SECONDS):
                QUEUE_FILE.unlink(missing_ok=True)
                return None
            return data
        except Exception:
            QUEUE_FILE.unlink(missing_ok=True)
    return None


def _write_lock(session_id: str):
    QUEUE_FILE.write_text(json.dumps({
        "session_id": session_id,
        "last_active": datetime.now().isoformat(),
    }))


def _release_lock(session_id: str):
    lock = _read_lock()
    if lock and lock["session_id"] == session_id:
        QUEUE_FILE.unlink(missing_ok=True)


def _heartbeat():
    lock = _read_lock()
    if lock and lock["session_id"] == st.session_state.chat_session_id:
        _write_lock(st.session_state.chat_session_id)


def acquire_or_check(session_id: str) -> tuple[bool, Optional[dict]]:
    with LOCK:
        current = _read_lock()
        if current is None:
            _write_lock(session_id)
            return True, None
        if current["session_id"] == session_id:
            _write_lock(session_id)
            return True, None
        return False, current


def is_queue_free() -> bool:
    return _read_lock() is None


# ---------------------------------------------------------------------------
# Ollama helpers
# ---------------------------------------------------------------------------
def ollama_is_running() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def chat_stream(messages: list[dict]):
    payload = {"model": MODEL, "messages": messages, "stream": True}
    with requests.post(f"{OLLAMA_URL}/api/chat", json=payload, stream=True, timeout=120) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line:
                chunk = json.loads(line)
                token = chunk.get("message", {}).get("content", "")
                if token:
                    yield token
                if chunk.get("done"):
                    break


# ---------------------------------------------------------------------------
# .doc binary text extraction helpers
# ---------------------------------------------------------------------------
import struct


def _extract_doc_text_olefile(raw: bytes) -> str:
    """
    Parse a .doc file using olefile by reading the FIB (File Information Block)
    and the piece table from the table stream to reconstruct the document text.
    """
    ole = olefile.OleFileIO(BytesIO(raw))
    try:
        # Read the WordDocument stream
        word_stream = ole.openstream("WordDocument")
        word_data = word_stream.read()

        if len(word_data) < 1024:
            return ""

        # FIB: read key offsets
        # Bytes 10-11: flags (bit 9 = fWhichTblStm, tells us 0Table vs 1Table)
        fib_flags = struct.unpack_from("<H", word_data, 0x000A)[0]
        use_1table = bool(fib_flags & 0x0200)
        table_name = "1Table" if use_1table else "0Table"

        if not ole.exists(table_name):
            return ""

        table_stream = ole.openstream(table_name)
        table_data = table_stream.read()

        # FIB: CLX (complex part) offset and size at 0x01A2 and 0x01A6
        clx_offset = struct.unpack_from("<I", word_data, 0x01A2)[0]
        clx_size = struct.unpack_from("<I", word_data, 0x01A6)[0]

        if clx_offset == 0 or clx_size == 0:
            return ""

        clx_data = table_data[clx_offset: clx_offset + clx_size]

        # Walk the CLX to find the Pcdt (piece table descriptor)
        pos = 0
        piece_table = None
        while pos < len(clx_data):
            clxt = clx_data[pos]
            if clxt == 0x02:  # Pcdt
                pcdt_size = struct.unpack_from("<I", clx_data, pos + 1)[0]
                piece_table = clx_data[pos + 5: pos + 5 + pcdt_size]
                break
            elif clxt == 0x01:  # Grpprl — skip
                grpprl_size = struct.unpack_from("<H", clx_data, pos + 1)[0]
                pos += 3 + grpprl_size
            else:
                break

        if piece_table is None:
            return ""

        # Parse piece table: array of CPs followed by array of PCDs
        # Number of pieces = (size - 4) / (4 + 8) ... each CP is 4 bytes, each PCD is 8 bytes
        # Actually: n+1 CPs (4 bytes each) + n PCDs (8 bytes each) where n = piece count
        # Total = (n+1)*4 + n*8 = 4 + 12n => n = (len - 4) / 12
        pt_len = len(piece_table)
        n_pieces = (pt_len - 4) // 12
        if n_pieces <= 0:
            return ""

        # Read character positions (n+1 of them)
        cps = []
        for i in range(n_pieces + 1):
            cp = struct.unpack_from("<I", piece_table, i * 4)[0]
            cps.append(cp)

        # Read piece descriptors (starting after CPs)
        pcd_offset = (n_pieces + 1) * 4
        text_parts = []
        for i in range(n_pieces):
            pcd_start = pcd_offset + i * 8
            if pcd_start + 8 > pt_len:
                break
            # PCD: 2 bytes flags, 4 bytes fc (file char position), 2 bytes prm
            fc_raw = struct.unpack_from("<I", piece_table, pcd_start + 2)[0]
            is_ansi = bool(fc_raw & 0x40000000)
            fc = fc_raw & 0x3FFFFFFF
            char_count = cps[i + 1] - cps[i]

            if is_ansi:
                # ANSI: 1 byte per character, fc is divided by 2
                start = fc // 2
                end = start + char_count
                if end <= len(word_data):
                    text_parts.append(
                        word_data[start:end].decode("cp1252", errors="replace")
                    )
            else:
                # Unicode: 2 bytes per character
                start = fc
                end = start + char_count * 2
                if end <= len(word_data):
                    text_parts.append(
                        word_data[start:end].decode("utf-16-le", errors="replace")
                    )

        full_text = "".join(text_parts)
        # Clean up Word control characters
        # \r = paragraph mark, \x07 = cell/row mark, \x0c = page break
        full_text = full_text.replace("\r", "\n")
        full_text = full_text.replace("\x07", "\t")
        full_text = full_text.replace("\x0c", "\n\n")
        full_text = full_text.replace("\x01", "")  # field begin
        full_text = full_text.replace("\x13", "")  # field separator
        full_text = full_text.replace("\x14", "")  # field separator
        full_text = full_text.replace("\x15", "")  # field end
        # Collapse excessive newlines
        full_text = re.sub(r'\n{3,}', '\n\n', full_text)
        return full_text.strip()
    except Exception:
        return ""
    finally:
        ole.close()


def _extract_doc_text_bruteforce(raw: bytes) -> str:
    """
    Last-resort: try multiple decodings of the entire binary and extract
    the longest readable text runs.
    """
    best = ""
    for encoding in ("utf-16-le", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding, errors="replace")
        except Exception:
            continue
        # Find runs of printable chars (including common punctuation and unicode)
        runs = re.findall(r'[\w \t.,;:!?\'\"()\-/\n]{10,}', text, re.UNICODE)
        # Keep only runs that look like prose (have spaces)
        prose = [r.strip() for r in runs if " " in r and len(r.strip()) > 20]
        candidate = "\n".join(prose)
        if len(candidate) > len(best):
            best = candidate
    return best


# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------
def extract_text(file) -> str:
    name = file.name.lower()
    file.seek(0)
    raw = file.read()

    if name.endswith(".txt") or name.endswith(".md"):
        return raw.decode("utf-8", errors="replace")

    if name.endswith(".pdf"):
        pdf_bytes = BytesIO(raw)
        if pdfplumber is not None:
            pages = []
            with pdfplumber.open(pdf_bytes) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    text = page.extract_text() or ""
                    if text.strip():
                        pages.append(f"--- Page {i} ---\n{text}")
            if pages:
                return "\n\n".join(pages)
            return "[WARNING] No text could be extracted from this PDF (it may be scanned/image-based)."
        if PdfReader is not None:
            reader = PdfReader(pdf_bytes)
            pages = []
            for i, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"--- Page {i} ---\n{text}")
            if pages:
                return "\n\n".join(pages)
            return "[WARNING] No text could be extracted from this PDF (it may be scanned/image-based)."
        return "[ERROR] No PDF library installed. Run: pip install pdfplumber"

    if name.endswith(".docx"):
        if DocxDocument is None:
            return "[ERROR] python-docx is not installed. Run: pip install python-docx"
        doc = DocxDocument(BytesIO(raw))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())

    if name.endswith(".doc"):
        # Strategy 1: antiword CLI (most reliable)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
        except Exception:
            pass
        if tmp_path:
            try:
                result = subprocess.run(
                    ["antiword", "-m", "UTF-8", tmp_path],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    os.unlink(tmp_path)
                    return result.stdout.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
            # Strategy 2: libreoffice headless
            try:
                subprocess.run(
                    ["libreoffice", "--headless", "--convert-to", "txt:Text",
                     "--outdir", "/tmp", tmp_path],
                    capture_output=True, text=True, timeout=30,
                )
                txt_out = "/tmp/" + os.path.basename(tmp_path).replace(".doc", ".txt")
                if os.path.exists(txt_out):
                    with open(txt_out, "r", errors="replace") as f:
                        content = f.read()
                    os.unlink(txt_out)
                    if content.strip():
                        os.unlink(tmp_path)
                        return content.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # Strategy 3: pure-Python .doc parser via olefile + piece table
        if olefile is not None:
            try:
                text = _extract_doc_text_olefile(raw)
                if text:
                    return text
            except Exception:
                pass

        # Strategy 4: brute-force binary text extraction (last resort)
        text = _extract_doc_text_bruteforce(raw)
        if text:
            return text

        return "[ERROR] Cannot extract text from this .doc file. Try converting it to .docx."

    return f"[Unsupported file type: {name}]"


def add_document(file):
    """Extract text, compute stats, store in session state. Returns True if new."""
    if file.name in st.session_state.documents:
        return False
    content = extract_text(file)
    wc = count_words(content)
    tc = estimate_tokens(content)
    st.session_state.documents[file.name] = {
        "content": content,
        "word_count": wc,
        "token_count": tc,
    }
    return True


# ---------------------------------------------------------------------------
# Build system prompt with document context
# ---------------------------------------------------------------------------
def build_system_prompt() -> str:
    parts = [
        "You are a helpful assistant. Answer the user's questions clearly and accurately.",
    ]
    if st.session_state.documents:
        parts.append(
            "\nThe user has uploaded the following documents. "
            "Use their content to answer questions when relevant.\n"
        )
        for doc_name, doc_info in st.session_state.documents.items():
            text = doc_info["content"]
            truncated = text[:80_000]
            suffix = "... [truncated]" if len(text) > 80_000 else ""
            parts.append(f"### Document: {doc_name}\n```\n{truncated}{suffix}\n```\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Export conversation
# ---------------------------------------------------------------------------
def export_conversation_md() -> str:
    """Generate markdown export from current session state. Always reads fresh."""
    messages = list(st.session_state.get("chat_messages", []))
    documents = dict(st.session_state.get("documents", {}))

    lines = [
        f"# Chat Export — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Model:** {MODEL}",
        f"**Documents:** {', '.join(documents.keys()) or 'None'}",
        f"**Messages:** {len(messages)}",
        "",
        "---",
        "",
    ]
    for i, msg in enumerate(messages, 1):
        role = "You" if msg["role"] == "user" else "Assistant"
        ts = msg.get("timestamp", "")
        dur = msg.get("duration")
        tokens = msg.get("tokens")
        meta_parts = []
        if ts:
            meta_parts.append(ts)
        if dur is not None:
            meta_parts.append(format_duration(dur))
        if tokens is not None:
            meta_parts.append(f"~{format_number(tokens)} tokens")
        meta = f" ({' · '.join(meta_parts)})" if meta_parts else ""
        lines.append(f"### {role}{meta}\n\n{msg['content']}\n")
    return "\n".join(lines)


def _parse_markdown_tables(text: str) -> list[tuple[str, list[list[str]]]]:
    """
    Find markdown tables in text. Returns list of (context_label, rows)
    where rows is a list of lists (header + data rows).
    """
    tables = []
    lines = text.split("\n")
    i = 0
    table_idx = 0
    while i < len(lines):
        line = lines[i].strip()
        # A markdown table row starts and ends with |
        if line.startswith("|") and line.endswith("|"):
            # Collect all consecutive table lines
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            if len(table_lines) >= 2:  # At least header + separator
                rows = []
                for tl in table_lines:
                    cells = [c.strip() for c in tl.strip("|").split("|")]
                    # Skip separator rows (---|---|---)
                    if all(re.match(r'^[-:]+$', c) for c in cells):
                        continue
                    rows.append(cells)
                if rows:
                    table_idx += 1
                    tables.append((f"Table {table_idx}", rows))
        else:
            i += 1
    return tables


def export_conversation_xlsx() -> Optional[bytes]:
    """
    Generate Excel export. Conversation goes on the main sheet.
    Any markdown tables found in assistant responses get their own tabs.
    Returns None if openpyxl is not installed or no messages.
    """
    if Workbook is None:
        return None
    messages = list(st.session_state.get("chat_messages", []))
    if not messages:
        return None

    wb = Workbook()

    # -- Sheet 1: Conversation --
    ws = wb.active
    ws.title = "Conversation"

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4A90D9", end_color="4A90D9", fill_type="solid")
    border = Border(
        bottom=Side(style="thin", color="D5D0C4"),
        right=Side(style="thin", color="D5D0C4"),
    )
    wrap = Alignment(wrap_text=True, vertical="top")

    headers = ["#", "Role", "Time", "Message", "Duration", "~Tokens"]
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 8
    ws.column_dimensions["D"].width = 80
    ws.column_dimensions["E"].width = 10
    ws.column_dimensions["F"].width = 10

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    all_tables = []
    for i, msg in enumerate(messages, 1):
        role = "You" if msg["role"] == "user" else "Assistant"
        row = i + 1
        ws.cell(row=row, column=1, value=i)
        ws.cell(row=row, column=2, value=role)
        ws.cell(row=row, column=3, value=msg.get("timestamp", ""))
        content_cell = ws.cell(row=row, column=4, value=msg["content"])
        content_cell.alignment = wrap
        dur = msg.get("duration")
        ws.cell(row=row, column=5, value=format_duration(dur) if dur is not None else "")
        tokens = msg.get("tokens")
        ws.cell(row=row, column=6, value=tokens if tokens is not None else "")

        for cell_ref in ws[row]:
            cell_ref.border = border

        # Collect tables from assistant messages
        if msg["role"] == "assistant":
            found = _parse_markdown_tables(msg["content"])
            for label, rows in found:
                all_tables.append((f"Msg{i} {label}", rows))

    # -- Additional sheets for tables --
    for sheet_name, rows in all_tables:
        # Sheet names max 31 chars
        safe_name = sheet_name[:31]
        ts = wb.create_sheet(title=safe_name)
        for r_idx, row_data in enumerate(rows, 1):
            for c_idx, val in enumerate(row_data, 1):
                cell = ts.cell(row=r_idx, column=c_idx, value=val)
                if r_idx == 1:
                    cell.font = Font(bold=True, color="FFFFFF", size=10)
                    cell.fill = PatternFill(start_color="4A90D9", end_color="4A90D9", fill_type="solid")
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                cell.border = border
        # Auto-width columns
        for col_cells in ts.columns:
            max_len = 0
            col_letter = col_cells[0].column_letter
            for cell in col_cells:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ts.column_dimensions[col_letter].width = min(max_len + 4, 50)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Message metadata renderer
# ---------------------------------------------------------------------------
def render_msg_meta(msg: dict):
    """Render subtle timestamp / duration / token info below a message."""
    parts = []
    ts = msg.get("timestamp")
    if ts:
        parts.append(f"<span>{ts}</span>")
    dur = msg.get("duration")
    if dur is not None:
        parts.append(f"<span>{format_duration(dur)}</span>")
    tokens = msg.get("tokens")
    if tokens is not None:
        parts.append(f"<span>~{format_number(tokens)} tokens</span>")
    if parts:
        st.markdown(f'<div class="msg-meta">{"".join(parts)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# LOBBY VIEW
# ---------------------------------------------------------------------------
def render_lobby():
    st.markdown("## Document Chat")
    st.caption("Chat with your documents using local Ollama models")

    free = is_queue_free()
    online = ollama_is_running()

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown('<div class="lobby-card">', unsafe_allow_html=True)
        st.markdown("#### Chatbot Status")

        if online:
            st.markdown(
                f'<span class="status-badge status-active">Ollama Online</span> '
                f'<span class="toolbar-meta">{MODEL}</span>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<span class="status-badge status-offline">Ollama Offline</span>',
                unsafe_allow_html=True,
            )

        st.write("")

        if free:
            st.markdown(
                '<span class="status-badge status-active">Available</span>',
                unsafe_allow_html=True,
            )
            st.caption("No one is using the chatbot right now.")
            st.write("")
            if st.button("Start Chat", use_container_width=True, key="start_chat", type="primary"):
                st.session_state.chat_session_id = hashlib.sha256(
                    f"{time.time()}".encode()
                ).hexdigest()[:16]
                st.session_state.chat_active = True
                st.rerun()
        else:
            st.markdown(
                '<span class="status-badge status-busy">In Use</span>',
                unsafe_allow_html=True,
            )
            st.caption(
                f"Someone is currently using the chatbot. "
                f"Sessions auto-release after {SESSION_TIMEOUT_SECONDS // 60} minutes of inactivity."
            )
            st.write("")
            if st.button("Refresh", use_container_width=True, key="lobby_refresh"):
                st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)



# ---------------------------------------------------------------------------
# TOOLBAR
# ---------------------------------------------------------------------------
def render_toolbar():
    online = ollama_is_running()
    doc_count = len(st.session_state.documents)

    # Total context stats
    total_words = sum(d["word_count"] for d in st.session_state.documents.values())
    total_tokens = sum(d["token_count"] for d in st.session_state.documents.values())

    c_brand, c_docs, c_actions, c_end = st.columns([3, 2, 2, 1.5])

    with c_brand:
        status_cls = "status-active" if online else "status-offline"
        status_txt = "Connected" if online else "Offline"
        st.markdown(
            f'<div class="toolbar-row">'
            f'<span class="toolbar-brand">Document Chat</span> '
            f'<span class="status-badge {status_cls}">{status_txt}</span> '
            f'<span class="toolbar-meta">{MODEL}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with c_docs:
        label = f"Documents ({doc_count})"
        if doc_count:
            label += f" \u00b7 ~{format_number(total_tokens)} tok"
        with st.popover(label, use_container_width=True):
            st.markdown('<p class="panel-section-title">Upload files</p>', unsafe_allow_html=True)
            uploaded = st.file_uploader(
                "Upload", type=["txt", "md", "pdf", "doc", "docx"],
                accept_multiple_files=True, key="chat_doc_uploader",
                label_visibility="collapsed",
            )
            if uploaded:
                new_added = False
                for f in uploaded:
                    if add_document(f):
                        new_added = True
                if new_added:
                    st.rerun()

            if st.session_state.documents:
                st.markdown('<hr class="divider">', unsafe_allow_html=True)
                st.markdown('<p class="panel-section-title">Loaded documents</p>', unsafe_allow_html=True)
                for doc_name in list(st.session_state.documents.keys()):
                    doc_info = st.session_state.documents[doc_name]
                    ext = Path(doc_name).suffix.lower()
                    icon = {".txt": "📄", ".md": "📝", ".pdf": "📕", ".doc": "📙", ".docx": "📘"}.get(ext, "📎")

                    dc1, dc2 = st.columns([5, 1])
                    with dc1:
                        st.markdown(f'<span class="doc-chip">{icon} {doc_name}</span>', unsafe_allow_html=True)
                    with dc2:
                        if st.button("✕", key=f"rm_{doc_name}", help=f"Remove {doc_name}"):
                            del st.session_state.documents[doc_name]
                            st.rerun()

                    # Preview card
                    preview_text = doc_info["content"][:500]
                    if len(doc_info["content"]) > 500:
                        preview_text += " ..."
                    # Escape HTML in preview
                    preview_text = (
                        preview_text
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                    )
                    st.markdown(
                        f'<div class="doc-preview-card">'
                        f'<div class="doc-preview-header">'
                        f'<span class="doc-preview-name">{icon} Preview</span>'
                        f'<div class="doc-preview-stats">'
                        f'<span>{format_number(doc_info["word_count"])} words</span>'
                        f'<span>~{format_number(doc_info["token_count"])} tokens</span>'
                        f'</div>'
                        f'</div>'
                        f'<div class="doc-preview-body">{preview_text}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

    with c_actions:
        with st.popover("Actions", use_container_width=True):
            if st.button("Clear Conversation", use_container_width=True, key="chat_clear"):
                st.session_state.chat_messages = []
                st.session_state.pop("_export_md", None)
                st.session_state.pop("_export_xlsx", None)
                st.rerun()

            st.markdown('<hr class="divider">', unsafe_allow_html=True)
            st.markdown('<p class="panel-section-title">Export</p>', unsafe_allow_html=True)

            # Prepare export on button click to avoid stale data
            if st.button("Prepare Export", use_container_width=True, key="chat_prepare_export"):
                st.session_state["_export_md"] = export_conversation_md()
                st.session_state["_export_xlsx"] = export_conversation_xlsx()
                st.rerun()

            # Show download buttons only when export data is ready
            md_data = st.session_state.get("_export_md")
            xlsx_data = st.session_state.get("_export_xlsx")

            if md_data:
                st.download_button(
                    "Download Markdown",
                    data=md_data,
                    file_name=f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="chat_export_md",
                )
            if xlsx_data:
                st.download_button(
                    "Download Excel",
                    data=xlsx_data,
                    file_name=f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="chat_export_xlsx",
                )
            elif Workbook is None and md_data:
                st.caption("Install openpyxl for Excel export")
            elif md_data and not xlsx_data:
                st.caption("No messages to export")

    with c_end:
        if st.button("End Session", use_container_width=True, key="chat_leave"):
            _release_lock(st.session_state.chat_session_id)
            st.session_state.chat_active = False
            st.session_state.chat_messages = []
            st.session_state.documents = {}
            st.session_state.pop("_export_md", None)
            st.session_state.pop("_export_xlsx", None)
            st.rerun()

    # Document chips bar
    if st.session_state.documents:
        chips_html = '<div class="doc-bar"><span class="doc-bar-label">Docs:</span>'
        for doc_name, doc_info in st.session_state.documents.items():
            ext = Path(doc_name).suffix.lower()
            icon = {".txt": "📄", ".md": "📝", ".pdf": "📕", ".doc": "📙", ".docx": "📘"}.get(ext, "📎")
            chips_html += (
                f'<span class="doc-chip">{icon} {doc_name}'
                f'<span style="color:var(--text-muted);font-size:0.7rem;margin-left:4px;">'
                f'{format_number(doc_info["word_count"])}w</span></span>'
            )
        chips_html += "</div>"
        st.markdown(chips_html, unsafe_allow_html=True)

    st.markdown('<hr class="divider" style="margin:0.25rem 0 0.5rem;">', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# CHAT AREA
# ---------------------------------------------------------------------------
def render_chat():
    doc_count = len(st.session_state.documents)

    if not st.session_state.chat_messages:
        greeting = "Ask me anything" if not doc_count else f"Ask about your {doc_count} document{'s' if doc_count != 1 else ''}"
        hint = (
            "Upload documents via the Documents button above, or just start a conversation."
            if not doc_count
            else "Your documents are loaded and ready. Start asking questions below."
        )
        st.markdown(
            f'<div class="empty-state"><h2>{greeting}</h2><p>{hint}</p></div>',
            unsafe_allow_html=True,
        )

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            render_msg_meta(msg)

    if prompt := st.chat_input(
        "Ask something..." if not doc_count else "Ask about your documents...",
        key="chat_input",
    ):
        if not ollama_is_running():
            st.error("Ollama is not reachable. Check the connection.")
            return

        _heartbeat()

        user_ts = datetime.now().strftime("%H:%M")
        user_tokens = estimate_tokens(prompt)
        user_msg = {
            "role": "user",
            "content": prompt,
            "timestamp": user_ts,
            "tokens": user_tokens,
        }
        st.session_state.chat_messages.append(user_msg)
        db_save_message(user_msg, st.session_state.chat_session_id,
                        st.session_state.get("username", ""),
                        list(st.session_state.documents.keys()))
        with st.chat_message("user"):
            st.markdown(prompt)
            render_msg_meta(user_msg)

        # Build API messages (strip metadata)
        system_msg = {"role": "system", "content": build_system_prompt()}
        api_messages = [system_msg] + [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state.chat_messages
        ]

        with st.chat_message("assistant"):
            placeholder = st.empty()
            meta_placeholder = st.empty()
            full_response = ""
            t_start = time.time()
            try:
                for token in chat_stream(api_messages):
                    full_response += token
                    placeholder.markdown(full_response + "▌")
                placeholder.markdown(full_response)
            except requests.exceptions.ConnectionError:
                full_response = "Connection to Ollama lost. Please check that it is running."
                placeholder.error(full_response)
            except Exception as e:
                full_response = f"Error: {e}"
                placeholder.error(full_response)

            duration = time.time() - t_start
            resp_tokens = estimate_tokens(full_response)
            resp_ts = datetime.now().strftime("%H:%M")
            assistant_msg = {
                "role": "assistant",
                "content": full_response,
                "timestamp": resp_ts,
                "duration": duration,
                "tokens": resp_tokens,
            }
            meta_placeholder.markdown(
                f'<div class="msg-meta">'
                f'<span>{resp_ts}</span>'
                f'<span>{format_duration(duration)}</span>'
                f'<span>~{format_number(resp_tokens)} tokens</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.session_state.chat_messages.append(assistant_msg)
        db_save_message(assistant_msg, st.session_state.chat_session_id,
                        st.session_state.get("username", ""),
                        list(st.session_state.documents.keys()))


# ---------------------------------------------------------------------------
# Admin view
# ---------------------------------------------------------------------------
def render_admin():
    """Full conversation history dashboard — visible to admins only, in lobby only."""
    st.markdown('<div class="history-section">', unsafe_allow_html=True)

    # -- Section header --
    hc1, hc2 = st.columns([6, 1])
    with hc1:
        st.markdown(
            '<div class="history-section-title">'
            '<h3>Conversation History</h3>'
            '<span class="admin-badge">Admin</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    with hc2:
        if st.button("Refresh", use_container_width=True, key="admin_refresh"):
            st.rerun()

    if psycopg2 is None:
        st.warning("psycopg2 is not installed. Run: `pip install psycopg2-binary`")
        st.markdown('</div>', unsafe_allow_html=True)
        return

    _test_conn = _get_conn()
    if _test_conn is None:
        st.warning("Could not connect to the database. Check your Vault / DB configuration.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    _test_conn.close()

    # -- Stats grid (6 cards) --
    stats = db_fetch_stats()
    avg_dur   = float(stats.get("avg_duration_s") or 0)
    max_dur   = float(stats.get("max_duration_s") or 0)
    first_msg = stats.get("first_message")
    last_msg  = stats.get("last_message")

    if stats:
        cs = st.columns(6)
        cards = [
            ("Sessions",       format_number(int(stats.get("total_sessions", 0))),
             f"First: {first_msg.strftime('%b %d') if first_msg else '—'}"),
            ("Total Messages", format_number(int(stats.get("total_messages", 0))),
             f"Last: {last_msg.strftime('%b %d') if last_msg else '—'}"),
            ("User",           format_number(int(stats.get("user_messages", 0))),
             "messages sent"),
            ("Assistant",      format_number(int(stats.get("assistant_messages", 0))),
             "responses given"),
            ("Avg Response",   format_duration(avg_dur),
             f"Max: {format_duration(max_dur)}"),
            ("Total Tokens",   format_number(int(stats.get("total_tokens", 0))),
             "estimated"),
        ]
        for col, (label, value, sub) in zip(cs, cards):
            with col:
                st.markdown(
                    f'<div class="stat-card">'
                    f'<div class="stat-value">{value}</div>'
                    f'<div class="stat-label">{label}</div>'
                    f'<div class="stat-sub">{sub}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # -- Charts --
    ts_rows = db_fetch_timeseries()
    if ts_rows:
        try:
            import pandas as pd
            df = pd.DataFrame(ts_rows)
            df["day"] = pd.to_datetime(df["day"])
            df = df.sort_values("day")
            df["avg_duration"] = df["avg_duration"].astype(float).round(2)
            df["tokens"] = df["tokens"].astype(int)

            st.markdown(
                '<div class="chart-section-divider"><span>Usage Over Time</span></div>',
                unsafe_allow_html=True,
            )

            ch1, ch2 = st.columns(2)

            with ch1:
                st.markdown('<div class="chart-card">', unsafe_allow_html=True)
                st.markdown('<div class="chart-card-title">Messages per Day</div>', unsafe_allow_html=True)
                chart_df = df.set_index("day")[["user_msgs", "assistant_msgs"]].rename(
                    columns={"user_msgs": "User", "assistant_msgs": "Assistant"}
                )
                st.bar_chart(chart_df, color=["#4a90d9", "#2d8a4e"], height=200)
                st.markdown('</div>', unsafe_allow_html=True)

            with ch2:
                st.markdown('<div class="chart-card">', unsafe_allow_html=True)
                st.markdown('<div class="chart-card-title">Avg Response Time (s)</div>', unsafe_allow_html=True)
                st.line_chart(
                    df.set_index("day")[["avg_duration"]].rename(columns={"avg_duration": "Avg (s)"}),
                    color=["#4a90d9"],
                    height=200,
                )
                st.markdown('</div>', unsafe_allow_html=True)

            ch3, ch4 = st.columns(2)

            with ch3:
                st.markdown('<div class="chart-card">', unsafe_allow_html=True)
                st.markdown('<div class="chart-card-title">Sessions per Day</div>', unsafe_allow_html=True)
                st.bar_chart(
                    df.set_index("day")[["sessions"]].rename(columns={"sessions": "Sessions"}),
                    color=["#4a90d9"],
                    height=180,
                )
                st.markdown('</div>', unsafe_allow_html=True)

            with ch4:
                st.markdown('<div class="chart-card">', unsafe_allow_html=True)
                st.markdown('<div class="chart-card-title">Tokens per Day (estimated)</div>', unsafe_allow_html=True)
                st.area_chart(
                    df.set_index("day")[["tokens"]].rename(columns={"tokens": "Tokens"}),
                    color=["#4a90d9"],
                    height=180,
                )
                st.markdown('</div>', unsafe_allow_html=True)

        except Exception:
            pass  # charts are best-effort; skip silently if pandas/data issue

    # -- User activity & Topics --
    user_rows = db_fetch_user_activity()
    session_topics = db_fetch_session_topics()

    if user_rows or session_topics:
        try:
            import pandas as pd

            if user_rows:
                st.markdown(
                    '<div class="chart-section-divider"><span>User Activity</span></div>',
                    unsafe_allow_html=True,
                )

                ua1, ua2 = st.columns(2)

                with ua1:
                    st.markdown('<div class="chart-card">', unsafe_allow_html=True)
                    st.markdown(
                        '<div class="chart-card-title">Messages by User</div>',
                        unsafe_allow_html=True,
                    )
                    udf = pd.DataFrame(user_rows)
                    chart_udf = udf.set_index("username")[["user_msgs", "assistant_msgs"]].rename(
                        columns={"user_msgs": "Sent", "assistant_msgs": "Received"}
                    )
                    st.bar_chart(chart_udf, color=["#4a90d9", "#2d8a4e"], horizontal=True, height=max(160, len(udf) * 38))
                    st.markdown('</div>', unsafe_allow_html=True)

                with ua2:
                    st.markdown('<div class="chart-card">', unsafe_allow_html=True)
                    st.markdown(
                        '<div class="chart-card-title">Tokens by User</div>',
                        unsafe_allow_html=True,
                    )
                    st.bar_chart(
                        udf.set_index("username")[["tokens"]].rename(columns={"tokens": "Tokens"}),
                        color=["#4a90d9"],
                        horizontal=True,
                        height=max(160, len(udf) * 38),
                    )
                    st.markdown('</div>', unsafe_allow_html=True)

                # User detail table
                st.markdown('<div class="chart-card">', unsafe_allow_html=True)
                st.markdown(
                    '<div class="chart-card-title">User Details</div>',
                    unsafe_allow_html=True,
                )
                table_data = []
                for u in user_rows:
                    last_ts = u["last_active"]
                    table_data.append({
                        "User": u["username"],
                        "Sessions": int(u["sessions"]),
                        "Messages": int(u["total_messages"]),
                        "Sent": int(u["user_msgs"]),
                        "Received": int(u["assistant_msgs"]),
                        "Tokens": format_number(int(u["tokens"])),
                        "First Active": u["first_active"].strftime("%Y-%m-%d %H:%M") if u["first_active"] else "—",
                        "Last Active": last_ts.strftime("%Y-%m-%d %H:%M") if last_ts else "—",
                    })
                st.dataframe(
                    pd.DataFrame(table_data),
                    use_container_width=True,
                    hide_index=True,
                )
                st.markdown('</div>', unsafe_allow_html=True)

            # Topic analysis
            if session_topics:
                st.markdown(
                    '<div class="chart-section-divider"><span>Topics &amp; Keywords</span></div>',
                    unsafe_allow_html=True,
                )

                tp1, tp2 = st.columns(2)

                with tp1:
                    keywords = _extract_topic_keywords(session_topics, top_n=15)
                    if keywords:
                        st.markdown('<div class="chart-card">', unsafe_allow_html=True)
                        st.markdown(
                            '<div class="chart-card-title">Top Keywords (from user prompts)</div>',
                            unsafe_allow_html=True,
                        )
                        kw_df = pd.DataFrame(keywords, columns=["Keyword", "Count"]).set_index("Keyword")
                        st.bar_chart(kw_df, color=["#4a90d9"], horizontal=True, height=max(180, len(keywords) * 28))
                        st.markdown('</div>', unsafe_allow_html=True)

                with tp2:
                    st.markdown('<div class="chart-card">', unsafe_allow_html=True)
                    st.markdown(
                        '<div class="chart-card-title">Session Openers (first question per session)</div>',
                        unsafe_allow_html=True,
                    )
                    for t in sorted(session_topics, key=lambda x: x["timestamp_utc"] or datetime.min, reverse=True)[:15]:
                        preview = t["content"][:120].replace("\n", " ").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        if len(t["content"]) > 120:
                            preview += " …"
                        ts_str = t["timestamp_utc"].strftime("%b %d, %H:%M") if t["timestamp_utc"] else ""
                        st.markdown(
                            f'<div style="padding:0.45rem 0;border-bottom:1px solid var(--border-light);">'
                            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">'
                            f'<span style="font-size:0.76rem;font-weight:600;color:var(--text-primary);">{t["username"]}</span>'
                            f'<span style="font-size:0.68rem;color:var(--text-muted);">{ts_str}</span>'
                            f'</div>'
                            f'<div style="font-size:0.8rem;color:var(--text-secondary);line-height:1.45;">{preview}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    st.markdown('</div>', unsafe_allow_html=True)
        except Exception:
            pass

    # -- Filter bar --
    sessions  = db_fetch_sessions()
    usernames = db_fetch_usernames()

    session_options  = ["All sessions"] + [s["session_id"] for s in sessions]
    username_options = ["All users"] + usernames

    st.markdown(
        '<div class="filter-bar">'
        '<div class="filter-bar-label">Filter history</div>',
        unsafe_allow_html=True,
    )
    fc1, fc2, fc3, fc4, fc5 = st.columns([2, 1.6, 1.2, 1.4, 1.4])
    with fc1:
        sel_session = st.selectbox(
            "Session",
            session_options,
            format_func=lambda s: s if s == "All sessions" else f"{s[:18]}…",
            key="admin_session_filter",
        )
    with fc2:
        sel_username = st.selectbox("User", username_options, key="admin_username_filter")
    with fc3:
        sel_role = st.selectbox("Role", ["All", "User", "Assistant"], key="admin_role_filter")
    with fc4:
        date_from = st.date_input("From date", value=None, key="admin_date_from")
    with fc5:
        date_to = st.date_input("To date", value=None, key="admin_date_to")
    st.markdown('</div>', unsafe_allow_html=True)

    # -- Fetch rows --
    rows = db_fetch_history(
        limit=500,
        session_filter=None if sel_session == "All sessions" else sel_session,
        username_filter=None if sel_username == "All users" else sel_username,
        role_filter=sel_role,
        date_from=datetime.combine(date_from, datetime.min.time()) if date_from else None,
        date_to=datetime.combine(date_to, datetime.max.time()) if date_to else None,
    )

    # -- Danger zone (admin only) --
    with st.expander("Danger Zone", expanded=False):
        st.warning("This will permanently delete **all** conversation history from the database.")
        confirm = st.checkbox("I understand, delete all history", key="admin_clear_confirm")
        if st.button(
            "Clear All History",
            disabled=not confirm,
            use_container_width=True,
            type="primary",
            key="admin_clear_all",
        ):
            if db_clear_all():
                st.success("All history deleted.")
                st.rerun()
            else:
                st.error("Failed to delete history. Check DB connection.")

    # -- Result count --
    if not rows:
        st.markdown(
            '<div style="text-align:center;padding:2rem;color:var(--text-muted);font-size:0.85rem;">'
            'No messages match the selected filters.'
            '</div>',
            unsafe_allow_html=True,
        )
        st.markdown('</div>', unsafe_allow_html=True)
        return

    n_sessions = len({r["session_id"] for r in rows})
    st.markdown(
        f'<div class="stat-meta-row">'
        f'<span><strong>{len(rows)}</strong> messages</span>'
        f'<span><strong>{n_sessions}</strong> session{"s" if n_sessions != 1 else ""}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # -- Tabs --
    tab_sessions, tab_flat = st.tabs(["By Session", "Flat Log"])

    # ---- By Session tab ----
    with tab_sessions:
        grouped: dict[str, list[dict]] = {}
        for r in rows:
            grouped.setdefault(r["session_id"], []).append(r)

        for sid, msgs in grouped.items():
            n_msgs    = len(msgs)
            first_ts  = msgs[-1]["timestamp_utc"]
            last_ts   = msgs[0]["timestamp_utc"]
            total_tok = sum(m["tokens_est"] or 0 for m in msgs)
            uname     = msgs[0].get("username") or "—"
            docs_list = msgs[0].get("documents") or []

            date_str = first_ts.strftime("%Y-%m-%d") if first_ts else "—"
            time_range = ""
            if first_ts and last_ts:
                time_range = f"{first_ts.strftime('%H:%M')} – {last_ts.strftime('%H:%M')}"

            label = f"{uname}  ·  {sid[:14]}…  ·  {n_msgs} messages  ·  {date_str}"
            with st.expander(label, expanded=False):
                # Session metadata header
                meta_parts = [
                    f'<span><strong>User:</strong> {uname}</span>',
                    f'<span><strong>Session:</strong> <span class="session-card-id">{sid}</span></span>',
                ]
                if time_range:
                    meta_parts.append(f'<span><strong>Time:</strong> {time_range}</span>')
                meta_parts.append(f'<span><strong>Tokens:</strong> ~{format_number(total_tok)}</span>')
                if docs_list:
                    doc_names = ", ".join(docs_list[:3])
                    if len(docs_list) > 3:
                        doc_names += f" +{len(docs_list) - 3} more"
                    meta_parts.append(f'<span><strong>Docs:</strong> {doc_names}</span>')
                st.markdown(
                    f'<div class="stat-meta-row" style="margin-bottom:0.75rem;">'
                    f'{"".join(meta_parts)}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # Chat replay
                for m in reversed(msgs):
                    ts_str  = m["timestamp_utc"].strftime("%H:%M:%S") if m["timestamp_utc"] else ""
                    dur_str = format_duration(float(m["duration_s"])) if m["duration_s"] else ""
                    tok_str = f"~{format_number(m['tokens_est'])} tok" if m["tokens_est"] else ""
                    with st.chat_message(m["role"]):
                        st.markdown(m["content"])
                        meta_items = [p for p in [ts_str, dur_str, tok_str] if p]
                        if meta_items:
                            st.markdown(
                                f'<div class="msg-meta">'
                                f'{"".join(f"<span>{p}</span>" for p in meta_items)}'
                                f'</div>',
                                unsafe_allow_html=True,
                            )

    # ---- Flat Log tab ----
    with tab_flat:
        for m in rows:
            role      = m["role"]
            role_cls  = "role-user" if role == "user" else "role-assistant"
            role_label = "User" if role == "user" else "Assistant"
            ts_str    = m["timestamp_utc"].strftime("%Y-%m-%d %H:%M:%S") if m["timestamp_utc"] else ""
            dur_str   = format_duration(float(m["duration_s"])) if m["duration_s"] else ""
            tok_str   = f"~{format_number(m['tokens_est'])} tok" if m["tokens_est"] else ""
            uname     = m.get("username") or "—"
            preview   = m["content"][:280].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")
            if len(m["content"]) > 280:
                preview += " …"

            meta_spans = "".join(
                f"<span>{p}</span>" for p in [ts_str, dur_str, tok_str] if p
            )
            st.markdown(
                f'<div class="history-row {role_cls}">'
                f'<div class="history-row-header">'
                f'<div style="display:flex;align-items:center;gap:8px;">'
                f'<span class="history-role-badge {role_cls}">{role_label}</span>'
                f'<span style="font-size:0.78rem;font-weight:600;color:var(--text-primary);">{uname}</span>'
                f'</div>'
                f'<div class="history-row-ident">'
                f'<span class="session-card-id">{m["session_id"][:16]}</span>'
                f'</div>'
                f'</div>'
                f'<div class="history-content">{preview}</div>'
                f'<div class="history-row-meta">{meta_spans}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------
def main():
    is_admin = "admin" in st.session_state.get("user_roles", {})

    if not st.session_state.chat_active:
        render_lobby()
        if is_admin:
            render_admin()
        return

    acquired, lock_info = acquire_or_check(st.session_state.chat_session_id)

    if not acquired:
        st.session_state.chat_active = False
        st.rerun()
        return

    render_toolbar()
    render_chat()


main()
