"""
Client IP via Request Headers — Streamlit Page
Reads the connected user's IP address from forwarded request headers
(X-Forwarded-For / X-Real-Ip) using the supported `st.context.headers` API.

This is the recommended approach for apps deployed behind a reverse proxy
(nginx, Traefik, a load balancer, etc.). The proxy must be configured to
inject the client IP into the header; the value is otherwise client-supplied
and spoofable.

Designed as a page within a multi-page Streamlit app.
"""

import streamlit as st


def _first_forwarded_ip(xff: str) -> str:
    """X-Forwarded-For is a comma-separated chain; the first entry is the
    original client, the rest are intermediary proxies."""
    return xff.split(",")[0].strip()


def get_client_ip() -> tuple[str | None, str]:
    """Return (ip, source) read from request headers.

    Falls back through the common header names. `source` records which header
    the value came from so the UI can be honest about provenance.
    """
    headers = st.context.headers or {}

    xff = headers.get("X-Forwarded-For")
    if xff:
        return _first_forwarded_ip(xff), "X-Forwarded-For"

    real_ip = headers.get("X-Real-Ip")
    if real_ip:
        return real_ip.strip(), "X-Real-Ip"

    return None, "(no forwarding header present)"


def render() -> None:
    st.title("🌐 Client IP — from request headers")
    st.caption(
        "Supported `st.context.headers` API. Best for deployments behind a "
        "reverse proxy that forwards the client IP."
    )

    ip, source = get_client_ip()

    if ip:
        st.metric("Detected client IP", ip)
        st.success(f"Read from the `{source}` header.")
    else:
        st.metric("Detected client IP", "unknown")
        st.warning(
            "No `X-Forwarded-For` or `X-Real-Ip` header was found. "
            "Either there is no reverse proxy in front of this app, or it is "
            "not configured to forward the client IP.\n\n"
            "**nginx example:** `proxy_set_header X-Forwarded-For "
            "$proxy_add_x_forwarded_for;`"
        )

    st.divider()

    with st.expander("⚠️ Security note", expanded=False):
        st.markdown(
            "- `X-Forwarded-For` is **client/proxy-controlled** and trivially "
            "spoofable unless your edge proxy strips any incoming value and "
            "sets it itself.\n"
            "- Do **not** use this for authentication or access control without "
            "that hardening.\n"
            "- Behind multiple proxies the header is a chain; the left-most "
            "entry is the claimed original client."
        )

    with st.expander("🔎 All request headers", expanded=False):
        headers = dict(st.context.headers or {})
        if headers:
            st.dataframe(
                [{"header": k, "value": v} for k, v in headers.items()],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No headers available in this context.")


# Render whether imported as a page (st.Page/st.navigation) or run directly.
render()
