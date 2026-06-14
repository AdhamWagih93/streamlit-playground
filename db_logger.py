"""
Session-state DB logger — with client IP detection.

Logs one row per interaction into the `session_states` Postgres table, now
enriched with the connected user's IP address and the method that detected it.

IP detection strategy (first match wins):
  1. Forwarding / proxy headers via the supported `st.context.headers` API —
     tried FIRST because the instance is usually deployed behind a reverse
     proxy. Checks the common header names in order of trust.
  2. Raw socket peer IP pulled from Streamlit's runtime internals — the
     fallback for when the instance runs WITHOUT a proxy.
  3. Gives up gracefully and records the IP as NULL with method "undetected".

The detected method is stored alongside the IP so the activity dashboard can
show *how* each IP was resolved.

NOTE: this adds two columns to the table — `client_ip` and `ip_method`.
If the table already exists, run once:
    ALTER TABLE session_states ADD COLUMN client_ip   TEXT;
    ALTER TABLE session_states ADD COLUMN ip_method    TEXT;
"""

import uuid
from datetime import datetime, timezone

import streamlit as st
import pandas as pd

from utils.postgres import get_engine

TABLE_NAME = "session_states"


# ---------------------------------------------------------------------------
# IP detection
# ---------------------------------------------------------------------------
def _parse_forwarded(value: str) -> str | None:
    """Parse the RFC 7239 `Forwarded` header, e.g. `for=1.2.3.4;proto=https`.
    Returns the first `for=` token, stripped of quotes/port/brackets."""
    for part in value.split(";"):
        part = part.strip()
        if part.lower().startswith("for="):
            token = part[4:].strip().strip('"')
            # IPv6 literals come wrapped like "[::1]:port"
            if token.startswith("["):
                return token[1:].split("]")[0]
            return token.split(":")[0]
    return None


# Header name -> extractor. Ordered most-specific/most-trusted first.
_HEADER_CHAIN = [
    ("X-Forwarded-For", lambda v: v.split(",")[0].strip()),  # left-most = original client
    ("X-Real-Ip", lambda v: v.strip()),
    ("CF-Connecting-IP", lambda v: v.strip()),               # Cloudflare
    ("True-Client-IP", lambda v: v.strip()),                 # Akamai / Cloudflare Enterprise
    ("Forwarded", _parse_forwarded),                         # RFC 7239
]


def _ip_from_headers() -> tuple[str | None, str | None]:
    """Option 1 — proxy/forwarding request headers (preferred)."""
    try:
        headers = st.context.headers or {}
    except Exception:
        return None, None

    for name, extract in _HEADER_CHAIN:
        raw = headers.get(name)
        if raw:
            try:
                ip = extract(raw)
            except Exception:
                ip = None
            if ip:
                return ip, f"header:{name}"
    return None, None


def _ip_from_socket() -> tuple[str | None, str | None]:
    """Option 2 — raw socket peer IP via Streamlit runtime internals (no proxy).

    Uses private, underscore-prefixed APIs that can change between Streamlit
    versions, so it is wrapped defensively and degrades to (None, None)."""
    try:
        from streamlit.runtime import get_instance
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        ctx = get_script_run_ctx()
        if ctx is None:
            return None, None
        session_info = get_instance()._session_mgr.get_session_info(ctx.session_id)
        if session_info is None:
            return None, None
        ip = session_info.client.request.remote_ip
        if ip:
            return ip, "socket_peer"
    except Exception:
        pass
    return None, None


def detect_client_ip() -> tuple[str | None, str]:
    """Resolve the connected user's IP, trying the proxy path first then the
    socket fallback. Returns (ip_or_None, method)."""
    ip, method = _ip_from_headers()
    if ip:
        return ip, method

    ip, method = _ip_from_socket()
    if ip:
        return ip, method

    return None, "undetected"


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
def log_session_state_db():
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    client_ip, ip_method = detect_client_ip()

    state_data = {
        "timestamp": now,
        "session_id": st.session_state.session_id,
        "company": st.session_state.get("company"),
        "username": st.session_state.get("username"),
        "original_user": st.session_state.get("original_user"),
        "current_page": st.session_state.get("current_page_name"),
        "client_ip": client_ip,
        "ip_method": ip_method,
    }

    df = pd.DataFrame([state_data])
    engine = get_engine()
    df.to_sql(TABLE_NAME, engine, if_exists="append", index=False)
