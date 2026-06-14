"""
Client IP via Socket Peer — Streamlit Page
Reads the connected user's TCP peer IP address straight from Streamlit's
runtime internals (the Tornado websocket request's `remote_ip`).

Use this only when there is NO reverse proxy in front of the app and you need
the actual socket peer. It relies on private, underscore-prefixed APIs
(`Runtime._session_mgr`) that can change between Streamlit versions — treat it
as best-effort and expect to revisit it on upgrades. Behind a proxy this just
returns the proxy's address; use the header-based page in that case.

Designed as a page within a multi-page Streamlit app.
"""

import streamlit as st


def get_remote_ip() -> str | None:
    """Return the TCP peer IP of the current session, or None if it cannot be
    resolved through the (private) runtime internals."""
    try:
        from streamlit.runtime import get_instance
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except Exception:
        return None

    try:
        ctx = get_script_run_ctx()
        if ctx is None:
            return None

        runtime = get_instance()
        session_info = runtime._session_mgr.get_session_info(ctx.session_id)
        if session_info is None:
            return None

        return session_info.client.request.remote_ip
    except Exception:
        # Internal API shape changed, or no client request available.
        return None


def render() -> None:
    st.title("🔌 Client IP — from socket peer")
    st.caption(
        "Reads the Tornado websocket `remote_ip` via Streamlit runtime "
        "internals. For direct (un-proxied) deployments only."
    )

    ip = get_remote_ip()

    if ip:
        st.metric("Socket peer IP", ip)
        st.success("Resolved from the live session's request object.")
    else:
        st.metric("Socket peer IP", "unknown")
        st.error(
            "Could not resolve the peer IP. This usually means the private "
            "Streamlit runtime API has changed in this version, or there is no "
            "active client request bound to the session."
        )

    st.divider()

    with st.expander("⚠️ Caveats", expanded=False):
        st.markdown(
            "- Uses **private** APIs (`Runtime._session_mgr`, "
            "`session_info.client.request`) that are **not** part of "
            "Streamlit's stable interface and may break on upgrade.\n"
            "- Behind a reverse proxy this returns the **proxy's** IP, not the "
            "end user's — use the header-based page (`X-Forwarded-For`) there.\n"
            "- Wrapped in defensive `try/except` so a future API change "
            "degrades to *unknown* instead of crashing the page."
        )


# Render whether imported as a page (st.Page/st.navigation) or run directly.
render()
