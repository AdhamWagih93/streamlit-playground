import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys

import os

import plotly.express as px
import pandas as pd
import streamlit as st
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.theme import set_theme


PAGE_TITLE = "Kubernetes"


_PLOTLY_TEMPLATE = "plotly_white"


def _style_fig(fig, *, height: int = 320):
    fig.update_layout(
        template=_PLOTLY_TEMPLATE,
        height=height,
        margin=dict(l=10, r=10, t=55, b=10),
        font=dict(family="Inter, Segoe UI, Arial, sans-serif", size=13, color="#0f172a"),
        title=dict(x=0.02, xanchor="left", font=dict(size=16, family="Inter, Segoe UI, Arial, sans-serif")),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0.0),
    )

    # A pleasant default palette (matches the page gradient vibes).
    try:
        fig.update_layout(colorway=px.colors.qualitative.Set2)
    except Exception:  # noqa: BLE001
        pass

    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(15,23,42,0.08)", zeroline=False)
    return fig


def _kpi_card(label: str, value: Any, *, tone: str = "neutral", help_text: str = "") -> None:
    """Render a colorful KPI card.

    tone: ok|warn|bad|info|neutral
    """

    tone_map = {
        "ok": {"bg": "rgba(34,197,94,0.12)", "border": "rgba(34,197,94,0.35)", "accent": "#16a34a"},
        "warn": {"bg": "rgba(245,158,11,0.14)", "border": "rgba(245,158,11,0.38)", "accent": "#d97706"},
        "bad": {"bg": "rgba(239,68,68,0.12)", "border": "rgba(239,68,68,0.35)", "accent": "#dc2626"},
        "info": {"bg": "rgba(14,165,233,0.12)", "border": "rgba(14,165,233,0.35)", "accent": "#0284c7"},
        "neutral": {"bg": "rgba(148,163,184,0.10)", "border": "rgba(148,163,184,0.35)", "accent": "#334155"},
    }
    t = tone_map.get(tone, tone_map["neutral"])

    html = f"""
    <div class="k8s-kpi" style="background:{t['bg']}; border:1px solid {t['border']};">
      <div class="k8s-kpi-label">{label}</div>
      <div class="k8s-kpi-value" style="color:{t['accent']};">{value}</div>
      {f"<div class='k8s-kpi-help'>{help_text}</div>" if help_text else ""}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def _table_explorer(
    title: str,
    rows: List[Dict[str, Any]],
    *,
    default_height: int = 380,
    key_prefix: str,
    default_sort_col: Optional[str] = None,
) -> None:
    """Dynamic table view: search, column picker, row limit, downloads."""

    st.markdown(f"<div class='k8s-section-title'>{title}</div>", unsafe_allow_html=True)

    if not rows:
        st.info("No rows.")
        return

    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No rows.")
        return

    # Compactly stringify dict/list columns for display
    for col in df.columns:
        if df[col].apply(lambda v: isinstance(v, (dict, list))).any():
            df[col] = df[col].apply(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        q = st.text_input("Search", value="", key=f"{key_prefix}_q", help="Filters rows by substring across all visible columns")
    with c2:
        limit = st.selectbox("Rows", options=[50, 100, 250, 500, 1000], index=2, key=f"{key_prefix}_limit")
    with c3:
        height = st.selectbox("Height", options=[260, 320, 380, 460, 560], index=2, key=f"{key_prefix}_height")

    all_cols = list(df.columns)
    default_cols = all_cols[: min(len(all_cols), 10)]
    cols = st.multiselect("Columns", options=all_cols, default=default_cols, key=f"{key_prefix}_cols")
    if cols:
        df = df[cols]

    if q.strip():
        ql = q.strip().lower()
        mask = df.astype(str).apply(lambda s: s.str.lower().str.contains(ql, na=False))
        df = df[mask.any(axis=1)]

    if default_sort_col and default_sort_col in df.columns:
        try:
            df = df.sort_values(default_sort_col)
        except Exception:  # noqa: BLE001
            pass

    df_view = df.head(int(limit))
    st.dataframe(df_view, use_container_width=True, hide_index=True, height=int(height))

    dcol1, dcol2 = st.columns(2)
    with dcol1:
        st.download_button(
            "Download CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name=f"{key_prefix}.csv",
            mime="text/csv",
            use_container_width=True,
            key=f"{key_prefix}_dl_csv",
        )
    with dcol2:
        st.download_button(
            "Download JSON",
            data=df.to_json(orient="records").encode("utf-8"),
            file_name=f"{key_prefix}.json",
            mime="application/json",
            use_container_width=True,
            key=f"{key_prefix}_dl_json",
        )
def _build_env() -> Dict[str, str]:
    from src.streamlit_config import StreamlitAppConfig

    cfg = StreamlitAppConfig.from_env()
    env: Dict[str, str] = {}
    kubeconfig = st.session_state.get("k8s_kubeconfig") or cfg.kubernetes.kubeconfig
    context = st.session_state.get("k8s_context") or cfg.kubernetes.context
    if kubeconfig:
        env["K8S_KUBECONFIG"] = kubeconfig
    if context:
        env["K8S_CONTEXT"] = context

    for key in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
        if key in os.environ and key not in env:
            env[key] = os.environ[key]
    return env


def _get_tools(env: Dict[str, str], force_reload: bool = False):
    """Load MCP tools and keep them in session_state (tools are not pickle-safe)."""

    sig = json.dumps(env, sort_keys=True)
    if force_reload or st.session_state.get("_k8s_tools_sig") != sig or "_k8s_tools" not in st.session_state:
        # Merge overrides onto the current process environment.
        # Some subprocess launchers treat provided env as a full replacement;
        # keeping the base env avoids Windows/Python startup surprises.
        subprocess_env = {**os.environ, **env}

        from src.streamlit_config import StreamlitAppConfig

        cfg = StreamlitAppConfig.from_env()
        transport = (cfg.kubernetes.mcp_transport or "stdio").lower().strip()
        transport = "sse" if transport == "http" else transport

        if transport == "stdio":
            conn = {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "src.ai.mcp_servers.kubernetes.mcp"],
                "env": subprocess_env,
            }
        else:
            conn = {
                "transport": "sse",
                "url": cfg.kubernetes.mcp_url,
            }
        client = MultiServerMCPClient(
            connections={
                "kubernetes": conn
            }
        )
        st.session_state["_k8s_tools"] = asyncio.run(client.get_tools())
        st.session_state["_k8s_tools_sig"] = sig
    return st.session_state["_k8s_tools"]


def _get_helm_tools(env: Dict[str, str], force_reload: bool = False):
    """Load Helm MCP tools and keep them in session_state (tools are not pickle-safe)."""

    from src.streamlit_config import StreamlitAppConfig

    cfg = StreamlitAppConfig.from_env()
    transport = (cfg.helm.mcp_transport or "stdio").lower().strip()
    transport = "sse" if transport == "http" else transport

    sig = json.dumps({"env": env, "transport": transport, "url": cfg.helm.mcp_url}, sort_keys=True)
    def _format_exc(e: BaseException) -> str:
        # Python 3.11+: MultiServerMCPClient uses TaskGroups; exceptions may be wrapped.
        try:
            if isinstance(e, BaseExceptionGroup):
                parts: List[str] = []
                for sub in e.exceptions:
                    parts.append(_format_exc(sub))
                joined = " | ".join(p for p in parts if p)
                return joined or str(e)
        except Exception:  # noqa: BLE001
            pass
        return str(e)

    def _load(conn: Dict[str, Any]):
        client = MultiServerMCPClient(connections={"helm": conn})
        return asyncio.run(client.get_tools())

    if force_reload or st.session_state.get("_helm_tools_sig") != sig or "_helm_tools" not in st.session_state:
        subprocess_env = {**os.environ, **env}

        conn_stdio = {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "src.ai.mcp_servers.helm.mcp"],
            "env": subprocess_env,
        }
        conn_remote = {
            "transport": "sse",
            "url": cfg.helm.mcp_url,
        }

        # Primary selection: configured transport.
        primary = conn_stdio if transport == "stdio" else conn_remote
        secondary = conn_stdio if primary is conn_remote else conn_remote

        try:
            st.session_state["_helm_tools"] = _load(primary)
        except Exception as exc:  # noqa: BLE001
            # Helpful fallback: if remote is configured but not reachable (common when running Streamlit locally),
            # fall back to stdio so the page is usable.
            if primary is conn_remote:
                try:
                    st.session_state["_helm_tools"] = _load(secondary)
                except Exception as exc2:  # noqa: BLE001
                    raise RuntimeError(_format_exc(exc2)) from exc2
            else:
                raise RuntimeError(_format_exc(exc)) from exc

        st.session_state["_helm_tools_sig"] = sig
    return st.session_state["_helm_tools"]


def _invoke_tool(tools, name: str, args: Dict[str, Any]) -> Any:
    def _matches(tool_name: str, desired: str) -> bool:
        if tool_name == desired:
            return True
        # LangChain MCP adapters often namespace tools to avoid collisions.
        # Common formats: "helm__list_releases", "helm.list_releases", "helm:list_releases", "helm_list_releases".
        for sep in ("__", ".", ":"):
            if sep in tool_name and tool_name.rsplit(sep, 1)[-1] == desired:
                return True
        if tool_name.endswith("_" + desired):
            return True
        return False

    tool = next((t for t in tools if _matches(str(getattr(t, "name", "")), name)), None)
    if tool is None:
        available = sorted({str(getattr(t, "name", "")) for t in (tools or []) if getattr(t, "name", None)})
        raise ValueError(f"Tool {name} not found. Available: {available}")

    if hasattr(tool, "ainvoke"):
        raw = asyncio.run(tool.ainvoke(args))
    else:
        raw = tool.invoke(args)

    return _normalise_mcp_result(raw)


def _normalise_mcp_result(value: Any) -> Any:
    """Normalise MCP tool results into plain Python data.

    Depending on the adapter/version, results may come back as:
    - a plain dict (ideal)
    - a list of content blocks: [{"type": "text", "text": "{...json...}"}, ...]
    - an object with a `.content` attribute containing those blocks
    """

    if isinstance(value, dict):
        return value

    if hasattr(value, "content"):
        try:
            return _normalise_mcp_result(getattr(value, "content"))
        except Exception:  # noqa: BLE001
            pass

    if isinstance(value, list):
        text_parts: List[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text_parts.append(item["text"])

        if text_parts:
            text = "\n".join(text_parts).strip()
            try:
                parsed = json.loads(text)
                return parsed
            except Exception:  # noqa: BLE001
                # Not JSON; still return structured content.
                return {"ok": True, "text": text}

    return value


def _as_list(result: Any, key: str) -> List[Dict[str, Any]]:
    if not isinstance(result, dict) or not result.get("ok"):
        return []
    value = result.get(key)
    return value if isinstance(value, list) else []


def _count_by(items: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        v = item.get(key) or "Unknown"
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def _top_counts(items: List[Dict[str, Any]], key: str, top_n: int = 10) -> List[Tuple[str, int]]:
    counts = _count_by(items, key)
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]


def _top_n_with_other(counts: Dict[str, int], top_n: int = 12, other_label: str = "Other") -> List[Tuple[str, int]]:
    items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    head = items[:top_n]
    tail = items[top_n:]
    other = sum(v for _, v in tail)
    if other > 0:
        head.append((other_label, other))
    return head


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:  # noqa: BLE001
        return 0


def _pod_phase_metrics(pods: List[Dict[str, Any]]) -> Dict[str, Any]:
    phase_counts = _count_by(pods, "phase")
    total = sum(phase_counts.values())
    running = phase_counts.get("Running", 0)
    pending = phase_counts.get("Pending", 0)
    failed = phase_counts.get("Failed", 0)
    unknown = phase_counts.get("Unknown", 0)
    return {
        "total": total,
        "running": running,
        "pending": pending,
        "failed": failed,
        "unknown": unknown,
        "phase_counts": phase_counts,
    }


def _deployment_health(deployments: List[Dict[str, Any]]) -> Dict[str, Any]:
    desired = 0
    ready = 0
    for d in deployments:
        desired += int(d.get("replicas") or 0)
        ready += int(d.get("readyReplicas") or 0)
    pct = (ready / desired * 100) if desired else 100.0
    return {"desired": desired, "ready": ready, "pct": pct}


def _render_tool_error(title: str, result: Any) -> None:
    st.error(title)
    with st.expander("Details", expanded=False):
        st.json(result)


def _extract_table_payload(result: Any) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
    """Extract common list payloads from kubectl_like responses."""

    if not isinstance(result, dict):
        return None, None
    payload = result.get("result") if isinstance(result.get("result"), dict) else None
    if payload is None:
        return None, None
    for key in ("pods", "nodes", "namespaces", "deployments", "services", "events", "service_accounts"):
        if key in payload and isinstance(payload[key], list):
            return key, payload[key]
    return None, None


def _stream_text(text: str):
    for line in (text or "").splitlines(True):
        yield line


def main() -> None:
    set_theme(PAGE_TITLE)

    # Page-level styling for a richer Kubernetes dashboard
    st.markdown(
        """
        <style>
        @keyframes k8sGradientShift {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }
        @keyframes k8sFadeIn {
            from { opacity: 0; transform: translateY(6px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @media (prefers-reduced-motion: reduce) {
            .k8s-hero, .k8s-card { animation: none !important; transition: none !important; }
        }
        .k8s-layout { max-width: 1200px; margin: 0 auto; }
        .k8s-hero {
            background: linear-gradient(120deg, #0b63d6, #22c55e, #0ea5e9, #a855f7);
            background-size: 300% 300%;
            border-radius: 18px;
            padding: 1.7rem 1.6rem 1.4rem 1.6rem;
            margin-bottom: 1.2rem;
            color: #fff;
            box-shadow: 0 12px 32px rgba(15, 23, 42, 0.35);
            animation: k8sGradientShift 10s ease-in-out infinite;
        }
        .k8s-hero-title {
            font-size: 1.7rem;
            font-weight: 800;
            letter-spacing: 0.06em;
            margin-bottom: 0.35rem;
        }
        .k8s-hero-sub { font-size: 0.95rem; opacity: 0.9; }
        .k8s-card {
            background: linear-gradient(145deg, #ffffff, #f3f6fb);
            border-radius: 18px;
            padding: 1.0rem 1.1rem 0.9rem 1.1rem;
            box-shadow: 0 6px 24px rgba(15, 23, 42, 0.10);
            border: 1px solid #d3ddec;
            margin-bottom: 1.0rem;
            animation: k8sFadeIn 260ms ease-out;
            transition: transform 160ms ease, box-shadow 160ms ease;
        }
        .k8s-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 34px rgba(15, 23, 42, 0.14);
        }
        .k8s-section-title {
            font-size: 1.1rem;
            font-weight: 700;
            margin-bottom: 0.4rem;
        }
        .k8s-pill {
            display: inline-block;
            padding: 0.20rem 0.55rem;
            border-radius: 999px;
            border: 1px solid #d3ddec;
            background: #f8fafc;
            color: #0f172a;
            font-size: 0.78rem;
            margin-right: 0.35rem;
            margin-bottom: 0.35rem;
        }
        .k8s-kpi {
            border-radius: 16px;
            padding: 0.75rem 0.85rem;
            box-shadow: 0 10px 26px rgba(15, 23, 42, 0.08);
        }
        .k8s-kpi-label {
            font-size: 0.82rem;
            opacity: 0.85;
            letter-spacing: 0.02em;
            margin-bottom: 0.25rem;
        }
        .k8s-kpi-value {
            font-size: 1.35rem;
            font-weight: 800;
            line-height: 1.1;
        }
        .k8s-kpi-help {
            font-size: 0.78rem;
            opacity: 0.85;
            margin-top: 0.25rem;
        }
        .k8s-health-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.55rem;
        }
        .k8s-health-item {
            border-radius: 14px;
            padding: 0.55rem 0.65rem;
            border: 1px solid rgba(148,163,184,0.35);
            background: rgba(248,250,252,0.65);
        }
        .k8s-health-title { font-weight: 700; font-size: 0.9rem; }
        .k8s-health-meta { opacity: 0.85; font-size: 0.78rem; margin-top: 0.15rem; }

        /* Terminal input styling (targeted by aria-label) */
        input[aria-label="kubectl-style command"] {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
            background: #0b1220 !important;
            color: #e2e8f0 !important;
            border: 1px solid rgba(148,163,184,0.35) !important;
            border-radius: 14px !important;
            padding: 0.55rem 0.7rem !important;
        }
        input[aria-label="kubectl-style command"]:focus {
            outline: none !important;
            box-shadow: 0 0 0 3px rgba(34,197,94,0.22) !important;
            border-color: rgba(34,197,94,0.6) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<div class='k8s-layout'>", unsafe_allow_html=True)

    st.markdown(
        """
        <div class="k8s-hero">
          <div class="k8s-hero-title">Kubernetes Control Center</div>
          <div class="k8s-hero-sub">
                        High-level cluster health, rich visualisations, and safe actions — all powered by the Kubernetes MCP server.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Kubernetes Connection")
        kubeconfig = st.text_input(
            "Kubeconfig path (optional)",
            value=st.session_state.get("k8s_kubeconfig", ""),
            help="Leave empty to use default kubeconfig (e.g. ~/.kube/config)",
        )
        context = st.text_input(
            "Context (optional)",
            value=st.session_state.get("k8s_context", ""),
            help="Optional Kubernetes context name",
        )
        st.session_state["k8s_kubeconfig"] = kubeconfig or ""
        st.session_state["k8s_context"] = context or ""

        col_a, col_b = st.columns(2)
        with col_a:
            reload_tools = st.button("Reload tools", type="primary", use_container_width=True, key="k8s_reload_tools")
        with col_b:
            refresh = st.button("Refresh data", use_container_width=True, key="k8s_refresh_data")

        st.caption("All Kubernetes interactions are executed through the MCP server.")

    env = _build_env()

    try:
        tools = _get_tools(env, force_reload=reload_tools)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load Kubernetes MCP tools: {exc}")
        with st.expander("Debug details", expanded=False):
            st.write("Command:")
            st.code(f"{sys.executable} -m src.ai.mcp_servers.kubernetes.mcp")
            st.write("Env overrides:")
            st.json(env)
            st.exception(exc)
        st.markdown("</div>", unsafe_allow_html=True)
        return

    # Snapshot (single refresh point to avoid repeated MCP calls during one render)
    if refresh or "k8s_snapshot" not in st.session_state:
        with st.spinner("Fetching cluster data via MCP…"):
            snapshot: Dict[str, Any] = {}
            snapshot["health"] = _invoke_tool(tools, "health_check", {})
            snapshot["stats"] = _invoke_tool(tools, "get_cluster_stats", {})
            snapshot["overview"] = _invoke_tool(tools, "get_cluster_overview", {})
            snapshot["namespaces"] = _invoke_tool(tools, "list_namespaces", {})
            snapshot["nodes"] = _invoke_tool(tools, "list_nodes", {})
            snapshot["pods"] = _invoke_tool(tools, "list_pods", {})
            snapshot["deployments"] = _invoke_tool(tools, "list_deployments_all", {})
            snapshot["services"] = _invoke_tool(tools, "list_services_all", {})
            snapshot["service_accounts"] = _invoke_tool(tools, "list_service_accounts_all", {})
            snapshot["events"] = _invoke_tool(tools, "list_events_all", {"limit": 250})
            st.session_state.k8s_snapshot = snapshot

    snapshot = st.session_state.get("k8s_snapshot", {})

    health = snapshot.get("health")
    stats = snapshot.get("stats")
    overview = snapshot.get("overview")
    namespaces = _as_list(snapshot.get("namespaces"), "namespaces")
    nodes = _as_list(snapshot.get("nodes"), "nodes")
    pods = _as_list(snapshot.get("pods"), "pods")
    deployments = _as_list(snapshot.get("deployments"), "deployments")
    services = _as_list(snapshot.get("services"), "services")
    service_accounts = _as_list(snapshot.get("service_accounts"), "service_accounts")
    events = _as_list(snapshot.get("events"), "events")

    st.markdown("<div class='k8s-card'>", unsafe_allow_html=True)

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown("<div class='k8s-section-title'>Cluster Summary</div>", unsafe_allow_html=True)

        # Health checks (reachability, version, basic API calls)
        if isinstance(health, dict) and health.get("ok"):
            version = health.get("version") if isinstance(health.get("version"), dict) else {}
            ver_str = version.get("gitVersion") or "(unknown)"
            st.markdown("<div class='k8s-pill'>Health</div>", unsafe_allow_html=True)
            hcol_a, hcol_b, hcol_c = st.columns([1, 1, 1])
            with hcol_a:
                _kpi_card("Reachable", "Yes", tone="ok", help_text=ver_str)
            with hcol_b:
                ms = version.get("ms")
                _kpi_card("API version", ver_str, tone="info", help_text=f"{ms} ms" if ms else "")
            with hcol_c:
                checks = health.get("checks") if isinstance(health.get("checks"), list) else []
                ok_checks = len([c for c in checks if isinstance(c, dict) and c.get("ok")])
                _kpi_card("Checks", f"{ok_checks}/{len(checks)}", tone="ok" if ok_checks == len(checks) else "warn")

            checks = health.get("checks") if isinstance(health.get("checks"), list) else []
            if checks:
                st.markdown("<div class='k8s-health-grid'>", unsafe_allow_html=True)
                for c in checks:
                    if not isinstance(c, dict):
                        continue
                    name = c.get("name")
                    ok = bool(c.get("ok"))
                    ms = c.get("ms")
                    err = c.get("error")
                    badge = "ok" if ok else "bad"
                    meta = f"{ms} ms" if ms is not None else ""
                    if err:
                        meta = (meta + " • " if meta else "") + str(err)[:120]
                    st.markdown(
                        f"""
                        <div class='k8s-health-item'>
                          <div class='k8s-health-title'>{name} <span style='color:{'#16a34a' if ok else '#dc2626'};'>●</span></div>
                          <div class='k8s-health-meta'>{meta}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                st.markdown("</div>", unsafe_allow_html=True)
        elif health is not None:
            _render_tool_error("Health check failed", health)

        if isinstance(overview, dict) and overview.get("ok"):
            pods_metrics = _pod_phase_metrics(pods)
            dep_metrics = _deployment_health(deployments)

            # Prefer server-side stats when available (RBAC-resilient), fall back to local counts.
            counts = stats.get("counts") if isinstance(stats, dict) and isinstance(stats.get("counts"), dict) else {}
            nodes_count = counts.get("nodes") if counts else len(nodes)
            ns_count = counts.get("namespaces") if counts else len(namespaces)
            pods_count = counts.get("pods") if counts else pods_metrics.get("total", 0)
            deploy_count = counts.get("deployments") if counts else len(deployments)
            svc_count = counts.get("services") if counts else len(services)
            sa_count = counts.get("serviceAccounts") if counts else len(service_accounts)

            not_ready_nodes = 0
            for node in nodes:
                ready = False
                for cond in node.get("conditions", []):
                    if cond.get("type") == "Ready" and cond.get("status") == "True":
                        ready = True
                        break
                if not ready:
                    not_ready_nodes += 1

            bad_pods = pods_metrics.get("failed", 0)

            kcol1, kcol2, kcol3, kcol4 = st.columns(4)
            with kcol1:
                _kpi_card("Nodes", nodes_count if nodes_count is not None else "–", tone="warn" if not_ready_nodes else "info", help_text=f"NotReady: {not_ready_nodes}")
            with kcol2:
                _kpi_card("Namespaces", ns_count if ns_count is not None else "–", tone="info")
            with kcol3:
                _kpi_card("Pods", pods_count if pods_count is not None else "–", tone="bad" if bad_pods else "info", help_text=f"Failed: {bad_pods}")
            with kcol4:
                _kpi_card("Deployments", deploy_count if deploy_count is not None else "–", tone="info")

            st.markdown(
                """
                <span class='k8s-pill'>Running</span>
                <span class='k8s-pill'>Pending</span>
                <span class='k8s-pill'>Failed</span>
                <span class='k8s-pill'>Services</span>
                <span class='k8s-pill'>Service Accounts</span>
                """,
                unsafe_allow_html=True,
            )

            s1, s2, s3, s4, s5, s6 = st.columns(6)
            with s1:
                _kpi_card("Running", pods_metrics["running"], tone="ok")
            with s2:
                _kpi_card("Pending", pods_metrics["pending"], tone="warn" if pods_metrics["pending"] else "neutral")
            with s3:
                _kpi_card("Failed", pods_metrics["failed"], tone="bad" if pods_metrics["failed"] else "neutral")
            with s4:
                _kpi_card("Services", svc_count if svc_count is not None else "–", tone="info")
            with s5:
                _kpi_card("Service Accts", sa_count if sa_count is not None else "–", tone="info")
            with s6:
                _kpi_card("Events", len(events), tone="neutral")

            if not_ready_nodes > 0 or pods_metrics.get("failed", 0) > 0:
                st.warning(f"Potential issues detected: {not_ready_nodes} NotReady node(s), {pods_metrics.get('failed', 0)} Failed pod(s).")
            else:
                st.info("No obvious issues detected (basic checks).")

            st.caption(f"Deployment readiness: {dep_metrics['ready']}/{dep_metrics['desired']} ready ({dep_metrics['pct']:.1f}%)")

            if isinstance(stats, dict) and stats.get("errors"):
                with st.expander("Stats warnings (RBAC/permissions)", expanded=False):
                    st.json(stats.get("errors"))
        else:
            _render_tool_error("Cluster overview failed", overview)

    with col2:
        st.markdown("<div class='k8s-section-title'>Quick Links</div>", unsafe_allow_html=True)
        st.write("Use the tabs below for dashboards, explorer views, per-resource actions, and a kubectl-like terminal.")
        if isinstance(health, dict) and health.get("checks"):
            with st.expander("Health check details", expanded=False):
                st.json(health)

    tabs = st.tabs([
        "Overview",
        "Namespaces",
        "Nodes",
        "Workloads",
        "Pods",
        "Services",
        "Events",
        "Helm",
        "Terminal",
    ])

    # ----- Tabs -----
    # Overview
    with tabs[0]:
        st.subheader("At a glance")
        # Show any partial overview errors without breaking the page.
        if isinstance(overview, dict) and overview.get("errors"):
            with st.expander("Cluster overview warnings", expanded=False):
                st.json(overview.get("errors"))
        if pods:
            phase_counts = _count_by(pods, "phase")
            fig = px.bar(
                x=list(phase_counts.keys()),
                y=list(phase_counts.values()),
                labels={"x": "Phase", "y": "Pods"},
                title="Pods by Phase",
            )
            fig = _style_fig(fig)
            st.plotly_chart(fig, use_container_width=True)

            ns_counts = _count_by(pods, "namespace")
            top_ns = _top_n_with_other(ns_counts, top_n=12)
            if top_ns:
                fig2 = px.bar(
                    x=[v for _, v in top_ns][::-1],
                    y=[k for k, _ in top_ns][::-1],
                    labels={"x": "Pods", "y": "Namespace"},
                    title="Namespaces by pod count (top 12 + Other)",
                    orientation="h",
                )
                fig2 = _style_fig(fig2, height=380)
                st.plotly_chart(fig2, use_container_width=True)

            # Hotspots: restarts
            restart_rows = []
            for p in pods:
                r = _safe_int(p.get("restarts"))
                if r > 0:
                    restart_rows.append({"pod": f"{p.get('namespace')}/{p.get('name')}", "restarts": r, "node": p.get("node")})
            restart_rows.sort(key=lambda r: r["restarts"], reverse=True)
            top_restarts = restart_rows[:15]
            if top_restarts:
                st.markdown("#### Hotspots")
                hcol1, hcol2 = st.columns([2, 1])
                with hcol1:
                    fig_r = px.bar(
                        x=[r["restarts"] for r in top_restarts][::-1],
                        y=[r["pod"] for r in top_restarts][::-1],
                        labels={"x": "Restarts", "y": "Pod"},
                        title="Top restarting pods",
                        orientation="h",
                    )
                    fig_r = _style_fig(fig_r, height=420)
                    st.plotly_chart(fig_r, use_container_width=True)
                with hcol2:
                    st.caption("Top restart pods")
                    st.dataframe(top_restarts, use_container_width=True, height=420)
        else:
            st.info("No pods data available.")

        if deployments:
            # Deployment readiness distribution
            rows: List[Dict[str, Any]] = []
            for d in deployments:
                desired = int(d.get("replicas") or 0)
                ready = int(d.get("readyReplicas") or 0)
                rows.append(
                    {
                        "namespace": d.get("namespace"),
                        "name": d.get("name"),
                        "desired": desired,
                        "ready": ready,
                        "unready": max(0, desired - ready),
                    }
                )
            ns_unready: Dict[str, int] = {}
            for r in rows:
                ns = str(r.get("namespace") or "(unknown)")
                ns_unready[ns] = ns_unready.get(ns, 0) + int(r.get("unready") or 0)

            top_unready = _top_n_with_other(ns_unready, top_n=12)
            if top_unready:
                fig3 = px.bar(
                    x=[v for _, v in top_unready][::-1],
                    y=[k for k, _ in top_unready][::-1],
                    labels={"x": "Unready replicas", "y": "Namespace"},
                    title="Unready replicas by namespace (top 12 + Other)",
                    orientation="h",
                )
                fig3 = _style_fig(fig3, height=380)
                st.plotly_chart(fig3, use_container_width=True)

    # Namespaces
    with tabs[1]:
        st.subheader("Namespaces")
        if namespaces:
            status_counts = _count_by(namespaces, "status")
            top_status = _top_n_with_other(status_counts, top_n=8)
            fig = px.bar(
                x=[v for _, v in top_status],
                y=[k for k, _ in top_status],
                labels={"x": "Count", "y": "Status"},
                title="Namespaces by status",
                orientation="h",
            )
            fig = _style_fig(fig, height=300)
            st.plotly_chart(fig, use_container_width=True)
        _table_explorer("Namespaces", namespaces, key_prefix="namespaces", default_sort_col="name")

        with st.expander("Namespace actions", expanded=False):
            c1, c2 = st.columns([2, 1])
            with c1:
                ns_to_create = st.text_input("Create namespace", value="", placeholder="e.g. staging", key="ns_create_name")
            with c2:
                create_confirm = st.checkbox("Confirm", key="ns_create_confirm")
            if st.button("Create namespace", type="primary", use_container_width=True, key="ns_create_btn"):
                if not ns_to_create.strip() or not create_confirm:
                    st.warning("Provide a namespace name and confirm.")
                else:
                    res = _invoke_tool(tools, "create_namespace", {"name": ns_to_create.strip()})
                    if isinstance(res, dict) and res.get("ok"):
                        st.success(f"Created namespace: {res.get('name')}")
                    else:
                        _render_tool_error("Create namespace failed", res)

    # Nodes
    with tabs[2]:
        st.subheader("Nodes")
        if not nodes:
            st.info("No nodes returned.")
        else:
            _table_explorer("Nodes", nodes, key_prefix="nodes", default_sort_col="name")
            ready_counts: Dict[str, int] = {"Ready": 0, "NotReady": 0}
            for node in nodes:
                ready = False
                for cond in node.get("conditions", []):
                    if cond.get("type") == "Ready" and cond.get("status") == "True":
                        ready = True
                        break
                ready_counts["Ready" if ready else "NotReady"] += 1
            fig = px.pie(names=list(ready_counts.keys()), values=list(ready_counts.values()), title="Node Readiness")
            fig = _style_fig(fig)
            fig.update_traces(hole=0.45, textinfo="percent+label")
            st.plotly_chart(fig, use_container_width=True)

    # Workloads
    with tabs[3]:
        st.subheader("Deployments")
        if not deployments:
            st.info("No deployments returned.")
        else:
            ns_options = sorted({d.get("namespace") for d in deployments if d.get("namespace")})
            selected_ns = st.multiselect(
                "Namespaces",
                options=ns_options,
                default=[],
                help="Type to search; leave empty for all.",
                key="deployments_ns_multi",
            )
            view = deployments if not selected_ns else [d for d in deployments if d.get("namespace") in set(selected_ns)]
            _table_explorer("Deployments", view, key_prefix="deployments")

            health = _deployment_health(view)
            st.caption(f"Ready replicas: {health['ready']}/{health['desired']} ({health['pct']:.1f}%)")

            with st.expander("Deployment actions", expanded=False):
                dep_choices = [f"{d.get('namespace')}/{d.get('name')}" for d in view if d.get("name") and d.get("namespace")]
                picked = st.selectbox("Deployment", options=[""] + sorted(dep_choices), key="dep_action_pick")
                act1, act2 = st.columns(2)
                with act1:
                    replicas = st.number_input("Replicas", min_value=0, max_value=10_000, value=1, step=1, key="dep_action_replicas")
                    confirm_scale = st.checkbox("Confirm scale", key="dep_action_scale_confirm")
                    if st.button("Scale", type="primary", use_container_width=True, key="dep_action_scale_btn"):
                        if not picked or not confirm_scale:
                            st.warning("Pick a deployment and confirm.")
                        else:
                            ns, name = picked.split("/", 1)
                            scale_result = _invoke_tool(tools, "scale_deployment", {"name": name, "namespace": ns, "replicas": int(replicas)})
                            if isinstance(scale_result, dict) and scale_result.get("ok"):
                                st.success(f"Scaled {ns}/{name} to {scale_result.get('replicas')} replicas.")
                            else:
                                _render_tool_error("Scaling failed", scale_result)
                with act2:
                    confirm_restart = st.checkbox("Confirm restart", key="dep_action_restart_confirm")
                    if st.button("Restart deployment", type="secondary", use_container_width=True, key="dep_action_restart_btn"):
                        if not picked or not confirm_restart:
                            st.warning("Pick a deployment and confirm.")
                        else:
                            ns, name = picked.split("/", 1)
                            restart_result = _invoke_tool(tools, "restart_deployment", {"name": name, "namespace": ns})
                            if isinstance(restart_result, dict) and restart_result.get("ok"):
                                st.success(f"Restart triggered for {ns}/{name}.")
                            else:
                                _render_tool_error("Restart failed", restart_result)

    # Pods
    with tabs[4]:
        st.subheader("Pods Explorer")
        if not pods:
            st.info("No pods returned.")
        else:
            ns_options = sorted({p.get("namespace") for p in pods if p.get("namespace")})
            phase_options = sorted({p.get("phase") for p in pods if p.get("phase")})
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                selected_ns = st.multiselect("Namespaces", options=ns_options, default=[], key="pods_ns_multi", help="Type to search; empty = all")
            with col_b:
                selected_phase = st.multiselect("Phases", options=phase_options, default=[], key="pods_phase_multi")
            with col_c:
                min_restarts = st.number_input("Min restarts", min_value=0, max_value=10_000, value=0, step=1, key="pods_min_restarts")

            view = pods
            if selected_ns:
                view = [p for p in view if p.get("namespace") in set(selected_ns)]
            if selected_phase:
                view = [p for p in view if p.get("phase") in set(selected_phase)]
            if min_restarts:
                view = [p for p in view if _safe_int(p.get("restarts")) >= int(min_restarts)]

            _table_explorer("Pods", view, key_prefix="pods")

            st.markdown("---")
            st.subheader("Pod Logs")
            # Prefer selecting from known pods to avoid typos
            pod_choices = [f"{p.get('namespace')}/{p.get('name')}" for p in view if p.get("name") and p.get("namespace")]
            pick = st.selectbox("Pod", options=[""] + sorted(pod_choices), key="pod_logs_pick")
            tail_lines = st.number_input("Tail lines", min_value=10, max_value=2000, value=200, step=10, key="pod_logs_tail")
            if st.button("Fetch logs", key="pod_logs_fetch"):
                if not pick:
                    st.warning("Select a pod first.")
                else:
                    ns, name = pick.split("/", 1)
                    log_result = _invoke_tool(tools, "get_pod_logs", {"name": name, "namespace": ns, "tail_lines": int(tail_lines)})
                    if isinstance(log_result, dict) and log_result.get("ok"):
                        st.text_area("Logs", value=log_result.get("logs", ""), height=360)
                    else:
                        _render_tool_error("Fetching logs failed", log_result)

            with st.expander("Pod actions", expanded=False):
                pod_choices = [f"{p.get('namespace')}/{p.get('name')}" for p in view if p.get("name") and p.get("namespace")]
                pod_pick = st.selectbox("Pod", options=[""] + sorted(pod_choices), key="pod_action_pick")
                confirm_delete = st.checkbox("Confirm delete", key="pod_action_delete_confirm")
                if st.button("Delete pod", type="secondary", use_container_width=True, key="pod_action_delete_btn"):
                    if not pod_pick or not confirm_delete:
                        st.warning("Pick a pod and confirm.")
                    else:
                        ns, name = pod_pick.split("/", 1)
                        del_result = _invoke_tool(tools, "delete_pod", {"name": name, "namespace": ns})
                        if isinstance(del_result, dict) and del_result.get("ok"):
                            st.success(f"Deleted pod {ns}/{name}.")
                        else:
                            _render_tool_error("Delete failed", del_result)

    # Services
    with tabs[5]:
        st.subheader("Services")
        if not services:
            st.info("No services returned.")
        else:
            _table_explorer("Services", services, key_prefix="services")
            type_counts = _count_by(services, "type")
            fig = px.bar(
                x=list(type_counts.keys()),
                y=list(type_counts.values()),
                labels={"x": "Type", "y": "Services"},
                title="Services by Type",
            )
            fig = _style_fig(fig)
            st.plotly_chart(fig, use_container_width=True)

    # Events
    with tabs[6]:
        st.subheader("Events")
        if not events:
            st.info("No events returned.")
        else:
            ns_options = sorted({e.get("namespace") for e in events if e.get("namespace")})
            selected_ns = st.multiselect("Namespaces", options=ns_options, default=[], key="events_ns_multi", help="Type to search; empty = all")
            view = events if not selected_ns else [e for e in events if e.get("namespace") in set(selected_ns)]
            _table_explorer("Events", view, key_prefix="events")
            reason_counts = _count_by(view, "reason")
            top_reasons = _top_n_with_other(reason_counts, top_n=14)
            if top_reasons:
                fig = px.bar(
                    x=[v for _, v in top_reasons][::-1],
                    y=[k for k, _ in top_reasons][::-1],
                    labels={"x": "Count", "y": "Reason"},
                    title="Event reasons (top 14 + Other)",
                    orientation="h",
                )
                fig = _style_fig(fig, height=420)
                st.plotly_chart(fig, use_container_width=True)

    # Helm
    with tabs[7]:
        st.subheader("Helm")
        st.caption("Helm inventory and actions via the Helm MCP server.")

        helm_env = _build_env()
        try:
            helm_tools = _get_helm_tools(helm_env)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Failed to load Helm MCP tools: {exc}")
            st.info("If running in Kubernetes, ensure the helm-mcp Deployment/Service exists and STREAMLIT_HELM_MCP_URL points to http://helm-mcp:8000/sse")
            helm_tools = []

        if not helm_tools:
            st.warning("Helm MCP tools are unavailable. Configure STREAMLIT_HELM_MCP_TRANSPORT=stdio for local dev, or deploy helm-mcp and set STREAMLIT_HELM_MCP_URL.")
            st.markdown("---")
            st.markdown("Nothing else to show until Helm MCP is reachable.")
            # Do not stop the whole page; only skip Helm tab content.
        else:

            top1, top2, top3 = st.columns([1, 1, 2])
            with top1:
                if st.button("Refresh Helm tools", use_container_width=True, key="helm_refresh_tools"):
                    helm_tools = _get_helm_tools(helm_env, force_reload=True)
                    st.success("Reloaded Helm tools")
            with top2:
                if st.button("Helm health check", use_container_width=True, key="helm_health"):
                    try:
                        hc = _invoke_tool(helm_tools, "health_check", {})
                        if isinstance(hc, dict) and hc.get("ok"):
                            st.success("Helm MCP is reachable")
                        st.json(hc)
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Helm health check failed: {exc}")
            with top3:
                st.info("Local dev defaults to your current kubeconfig context. For remote clusters, point Helm MCP at a kubeconfig/context or run it in-cluster.")

            with st.expander("Show loaded tool names", expanded=False):
                names = sorted({str(getattr(t, "name", "")) for t in (helm_tools or []) if getattr(t, "name", None)})
                if not names:
                    st.write("No tool names found.")
                else:
                    st.code("\n".join(names), language="text")

            st.markdown("#### Releases")

            rcol1, rcol2, rcol3, rcol4 = st.columns([2, 1, 1, 1])
            with rcol1:
                ns_names = sorted({str(n.get("name")) for n in (namespaces or []) if n.get("name")})
                ns_filter = st.selectbox("Namespace", options=["(all)"] + ns_names, index=0, key="helm_ns_filter")
            with rcol2:
                all_namespaces = st.checkbox("All namespaces", value=True, key="helm_all_ns")
            with rcol3:
                auto_refresh = st.checkbox("Auto refresh", value=False, key="helm_auto_refresh")
            with rcol4:
                if st.button("Refresh releases", use_container_width=True, key="helm_refresh_releases"):
                    st.session_state["_helm_releases_cache"] = None

            if auto_refresh or st.session_state.get("_helm_releases_cache") is None:
                try:
                    args: Dict[str, Any] = {"all_namespaces": bool(all_namespaces)}
                    if not all_namespaces and ns_filter != "(all)":
                        args["namespace"] = ns_filter
                    # Some adapters namespace tool names (e.g. "helm__list_releases").
                    rel_tool_name = "list_releases"
                    for t in helm_tools or []:
                        tn = str(getattr(t, "name", ""))
                        if tn == "list_releases" or tn.endswith("__list_releases") or tn.endswith(".list_releases") or tn.endswith(":list_releases") or tn.endswith("_list_releases"):
                            rel_tool_name = tn
                            break
                    rel_result = _invoke_tool(helm_tools, rel_tool_name, args)
                    st.session_state["_helm_releases_cache"] = rel_result
                except Exception as exc:  # noqa: BLE001
                    st.session_state["_helm_releases_cache"] = {"ok": False, "error": str(exc)}

            rel_result = st.session_state.get("_helm_releases_cache")
            releases = _as_list(rel_result, "releases")
            if not isinstance(rel_result, dict) or not rel_result.get("ok"):
                st.error("Failed to list releases")
                st.json(rel_result)
            elif not releases:
                st.info("No releases found.")
            else:
                status_counts = _count_by(releases, "status")
                k1, k2, k3, k4 = st.columns(4)
                with k1:
                    _kpi_card("Releases", len(releases), tone="info")
                with k2:
                    _kpi_card("Deployed", status_counts.get("deployed", 0), tone="ok")
                with k3:
                    _kpi_card("Failed", status_counts.get("failed", 0), tone="bad" if status_counts.get("failed", 0) else "neutral")
                with k4:
                    _kpi_card(
                        "Pending",
                        sum(v for k, v in status_counts.items() if str(k).startswith("pending")),
                        tone="warn",
                    )

                _table_explorer("Helm releases", releases, key_prefix="helm_releases", default_sort_col="namespace")

            st.markdown("#### Release details")
            release_choices: List[str] = []
            for r in releases:
                name = r.get("name")
                ns = r.get("namespace")
                if name:
                    release_choices.append(f"{ns}/{name}" if ns else str(name))
            release_choices = sorted(set(release_choices))

            det1, det2 = st.columns([2, 1])
            with det1:
                picked = st.selectbox("Release", options=[""] + release_choices, index=0, key="helm_release_pick")
            with det2:
                show_all_values = st.checkbox("All values", value=False, key="helm_all_values")

            if picked:
                if "/" in picked:
                    picked_ns, picked_name = picked.split("/", 1)
                else:
                    picked_ns, picked_name = None, picked

                a1, a2, a3, a4 = st.columns(4)
                with a1:
                    if st.button("Status", use_container_width=True, key="helm_btn_status"):
                        st.session_state["_helm_last_status"] = _invoke_tool(
                            helm_tools,
                            "get_release_status",
                            {"release": picked_name, "namespace": picked_ns},
                        )
                with a2:
                    if st.button("History", use_container_width=True, key="helm_btn_history"):
                        st.session_state["_helm_last_history"] = _invoke_tool(
                            helm_tools,
                            "get_release_history",
                            {"release": picked_name, "namespace": picked_ns, "max_entries": 25},
                        )
                with a3:
                    if st.button("Values", use_container_width=True, key="helm_btn_values"):
                        st.session_state["_helm_last_values"] = _invoke_tool(
                            helm_tools,
                            "get_release_values",
                            {"release": picked_name, "namespace": picked_ns, "all_values": bool(show_all_values)},
                        )
                with a4:
                    if st.button("Manifest", use_container_width=True, key="helm_btn_manifest"):
                        st.session_state["_helm_last_manifest"] = _invoke_tool(
                            helm_tools,
                            "get_release_manifest",
                            {"release": picked_name, "namespace": picked_ns},
                        )

                out_tabs = st.tabs(["Status", "History", "Values", "Manifest"])
                with out_tabs[0]:
                    st.json(st.session_state.get("_helm_last_status") or {"info": "Click Status"})
                with out_tabs[1]:
                    st.json(st.session_state.get("_helm_last_history") or {"info": "Click History"})
                with out_tabs[2]:
                    vv = st.session_state.get("_helm_last_values")
                    if isinstance(vv, dict) and "values_text" in vv:
                        st.code(str(vv.get("values_text", "")), language="yaml")
                    else:
                        st.json(vv or {"info": "Click Values"})
                with out_tabs[3]:
                    mm = st.session_state.get("_helm_last_manifest")
                    if isinstance(mm, dict) and mm.get("ok") and isinstance(mm.get("text"), str):
                        st.code(mm.get("text"), language="yaml")
                    else:
                        st.json(mm or {"info": "Click Manifest"})

                st.markdown("##### Uninstall")
                u1, u2, u3 = st.columns([2, 1, 1])
                with u1:
                    confirm = st.checkbox("I understand this deletes the release", value=False, key="helm_uninstall_confirm")
                with u2:
                    keep_history = st.checkbox("Keep history", value=False, key="helm_keep_history")
                with u3:
                    if st.button("Uninstall", use_container_width=True, disabled=not confirm, key="helm_uninstall"):
                        res = _invoke_tool(
                            helm_tools,
                            "uninstall_release",
                            {
                                "release": picked_name,
                                "namespace": picked_ns,
                                "keep_history": bool(keep_history),
                                "wait": True,
                                "timeout": "5m",
                            },
                        )
                        st.json(res)
                        st.session_state["_helm_releases_cache"] = None

            st.markdown("#### Install / Upgrade")
            with st.form("helm_upgrade_install"):
                f1, f2, f3 = st.columns([1, 2, 1])
                with f1:
                    rel_name = st.text_input("Release name", value="", placeholder="my-release")
                with f2:
                    chart = st.text_input("Chart", value="", placeholder="bitnami/nginx or ./chart")
                with f3:
                    ns = st.text_input("Namespace", value="default")

                g1, g2, g3, g4 = st.columns(4)
                with g1:
                    version = st.text_input("Version (optional)", value="")
                with g2:
                    wait = st.checkbox("Wait", value=True)
                with g3:
                    atomic = st.checkbox("Atomic", value=False)
                with g4:
                    dry_run = st.checkbox("Dry run", value=False)

                values_yaml = st.text_area("Values YAML (optional)", value="", height=160)
                set_values_json = st.text_area("--set values as JSON (optional)", value="{}", height=120)

                submitted = st.form_submit_button("Run upgrade --install", use_container_width=True)

            if submitted:
                if not rel_name.strip() or not chart.strip():
                    st.error("Release name and chart are required")
                else:
                    try:
                        set_values: Dict[str, Any]
                        set_values = json.loads(set_values_json or "{}")
                        if not isinstance(set_values, dict):
                            raise ValueError("--set JSON must be an object")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Invalid --set JSON: {exc}")
                        set_values = {}

                    res = _invoke_tool(
                        helm_tools,
                        "upgrade_install_release",
                        {
                            "release": rel_name.strip(),
                            "chart": chart.strip(),
                            "namespace": ns.strip() or "default",
                            "create_namespace": True,
                            "version": version.strip() or None,
                            "values_yaml": values_yaml or None,
                            "values_files": [],
                            "set_values": set_values,
                            "wait": bool(wait),
                            "atomic": bool(atomic),
                            "timeout": "10m",
                            "dry_run": bool(dry_run),
                        },
                    )
                    st.json(res)
                    st.session_state["_helm_releases_cache"] = None

            st.markdown("#### Repositories & Search")
            rr1, rr2, rr3 = st.columns([1, 1, 2])
            with rr1:
                if st.button("List repos", use_container_width=True, key="helm_repo_list"):
                    st.session_state["_helm_repos"] = _invoke_tool(helm_tools, "repo_list", {})
            with rr2:
                if st.button("Repo update", use_container_width=True, key="helm_repo_update"):
                    st.session_state["_helm_repo_update"] = _invoke_tool(helm_tools, "repo_update", {})
            with rr3:
                st.write("")

            if st.session_state.get("_helm_repos"):
                repos_res = st.session_state.get("_helm_repos")
                repos = _as_list(repos_res, "repos")
                if isinstance(repos_res, dict) and repos_res.get("ok"):
                    _table_explorer("Helm repos", repos, key_prefix="helm_repos")
                else:
                    st.json(repos_res)

            if st.session_state.get("_helm_repo_update"):
                st.json(st.session_state.get("_helm_repo_update"))

            with st.expander("Add repo", expanded=False):
                rname = st.text_input("Repo name", value="", key="helm_repo_name")
                rurl = st.text_input("Repo URL", value="", key="helm_repo_url")
                ruser = st.text_input("Username (optional)", value="", key="helm_repo_user")
                rpass = st.text_input("Password (optional)", value="", type="password", key="helm_repo_pass")
                if st.button("Add", use_container_width=True, key="helm_repo_add"):
                    res = _invoke_tool(
                        helm_tools,
                        "repo_add",
                        {"name": rname, "url": rurl, "username": ruser or None, "password": rpass or None},
                    )
                    st.json(res)

            s1, s2, s3 = st.columns([3, 1, 1])
            with s1:
                search_q = st.text_input("Search repo", value="", placeholder="nginx", key="helm_search_q")
            with s2:
                include_versions = st.checkbox("All versions", value=False, key="helm_search_versions")
            with s3:
                if st.button("Search", use_container_width=True, key="helm_search") and search_q.strip():
                    res = _invoke_tool(helm_tools, "search_repo", {"query": search_q.strip(), "versions": bool(include_versions)})
                    matches = _as_list(res, "matches")
                    if isinstance(res, dict) and res.get("ok"):
                        _table_explorer("Search results", matches, key_prefix="helm_search")
                    else:
                        st.json(res)

    # Terminal
    with tabs[8]:
        st.subheader("Kubernetes Terminal")
        st.caption("A kubectl-style console powered by the Kubernetes MCP server (no direct kubectl execution).")

        if "k8s_terminal_history" not in st.session_state:
            st.session_state.k8s_terminal_history = []  # type: ignore[assignment]

        col_a, col_b = st.columns([1, 1])
        with col_a:
            if st.button("Clear terminal history", use_container_width=True, key="k8s_term_clear"):
                st.session_state.k8s_terminal_history = []  # type: ignore[assignment]
        with col_b:
            st.caption("Supported: get ... (-A supported) · logs · delete pod · scale deployment · create namespace")

        history = st.session_state.k8s_terminal_history  # type: ignore[assignment]
        if "k8s_terminal_selected" not in st.session_state:
            st.session_state.k8s_terminal_selected = -1  # type: ignore[assignment]

        left, right = st.columns([1, 2])
        with left:
            st.markdown("#### History")
            if not history:
                st.caption("No commands yet.")
            else:
                for i, h in enumerate(reversed(history[-30:])):
                    real_i = len(history) - 1 - i
                    label = h.get("command", "")
                    ok = bool(h.get("ok"))
                    tone = "ok" if ok else "bad"
                    if st.button(label[:42] + ("…" if len(label) > 42 else ""), use_container_width=True, key=f"k8s_hist_{real_i}"):
                        st.session_state.k8s_terminal_selected = real_i  # type: ignore[assignment]

        with right:
            idx = int(st.session_state.k8s_terminal_selected)  # type: ignore[arg-type]
            if history and (idx < 0 or idx >= len(history)):
                idx = len(history) - 1
            if not history:
                st.info("Run a command to see output here.")
            else:
                entry = history[idx]
                st.markdown("#### Console")
                st.code(entry.get("command", ""), language="bash")
                if not entry.get("ok"):
                    st.error(entry.get("error") or "Command failed")

                output_tabs = st.tabs(["Table", "Text", "Raw"])
                with output_tabs[0]:
                    rows = entry.get("table_rows")
                    if rows is not None:
                        _table_explorer("Table output", rows, key_prefix=f"term_table_{idx}", default_height=320)
                    else:
                        st.info("No tabular output for this command.")

                with output_tabs[1]:
                    txt = entry.get("text") or ""
                    lang = entry.get("text_lang") or "text"
                    if txt:
                        st.code(txt, language=lang)
                    else:
                        st.info("No text output for this command.")

                with output_tabs[2]:
                    st.json(entry.get("raw"))

        st.markdown("---")

        with st.expander("Command builder", expanded=False):
            b_col1, b_col2, b_col3, b_col4 = st.columns([1, 1, 1, 1])
            with b_col1:
                b_verb = st.selectbox("Verb", options=["get", "logs", "delete", "scale", "create"], key="k8s_term_builder_verb")
            with b_col2:
                if b_verb == "get":
                    b_resource = st.selectbox(
                        "Resource",
                        options=["pods", "nodes", "namespaces", "deployments", "services", "events", "sa"],
                        key="k8s_term_builder_resource_get",
                    )
                elif b_verb == "create":
                    b_resource = st.selectbox("Resource", options=["namespace"], key="k8s_term_builder_resource_create")
                elif b_verb == "scale":
                    b_resource = "deployment"
                    st.selectbox("Resource", options=["deployment"], index=0, key="k8s_term_builder_resource_scale", disabled=True)
                elif b_verb == "delete":
                    b_resource = "pod"
                    st.selectbox("Resource", options=["pod"], index=0, key="k8s_term_builder_resource_delete", disabled=True)
                else:
                    b_resource = "pod"
                    st.selectbox("Resource", options=["pod"], index=0, key="k8s_term_builder_resource_logs", disabled=True)

            with b_col3:
                b_all_ns = False
                if b_verb == "get" and b_resource not in ("nodes", "namespaces"):
                    b_all_ns = st.checkbox("All namespaces (-A)", value=True if b_resource in ("deployments",) else False, key="k8s_term_builder_allns")
                b_namespace = st.text_input("Namespace (-n)", value="default", key="k8s_term_builder_ns")

            with b_col4:
                b_output = st.selectbox("Output (-o)", options=["table", "wide", "yaml", "json"], key="k8s_term_builder_out")

            pods_for_picker = [f"{p.get('namespace')}/{p.get('name')}" for p in (pods or []) if p.get("namespace") and p.get("name")]
            deps_for_picker = [f"{d.get('namespace')}/{d.get('name')}" for d in (deployments or []) if d.get("namespace") and d.get("name")]

            example_cmd = ""
            if b_verb == "get":
                if b_resource in ("nodes", "namespaces"):
                    example_cmd = f"get {b_resource} -o {b_output}"
                else:
                    ns_part = "-A" if b_all_ns else f"-n {b_namespace}"
                    example_cmd = f"get {b_resource} {ns_part} -o {b_output}"
            elif b_verb == "logs":
                pick_pod = st.selectbox("Pod", options=[""] + sorted(pods_for_picker), key="k8s_term_builder_pod_logs")
                if pick_pod:
                    ns, name = pick_pod.split("/", 1)
                    example_cmd = f"logs {name} -n {ns} --tail=200"
                else:
                    example_cmd = "logs <pod-name> -n default --tail=200"
            elif b_verb == "delete":
                pick_pod = st.selectbox("Pod", options=[""] + sorted(pods_for_picker), key="k8s_term_builder_pod_delete")
                if pick_pod:
                    ns, name = pick_pod.split("/", 1)
                    example_cmd = f"delete pod {name} -n {ns}"
                else:
                    example_cmd = "delete pod <pod-name> -n default"
            elif b_verb == "scale":
                pick_dep = st.selectbox("Deployment", options=[""] + sorted(deps_for_picker), key="k8s_term_builder_dep")
                rep = st.number_input("Replicas", min_value=0, max_value=10_000, value=2, step=1, key="k8s_term_builder_dep_rep")
                if pick_dep:
                    ns, name = pick_dep.split("/", 1)
                    example_cmd = f"scale deployment {name} -n {ns} --replicas={int(rep)}"
                else:
                    example_cmd = f"scale deployment <deploy-name> -n default --replicas={int(rep)}"
            elif b_verb == "create":
                ns_name = st.text_input("Name", value="", placeholder="e.g. staging", key="k8s_term_builder_create_ns")
                if ns_name.strip():
                    example_cmd = f"create namespace {ns_name.strip()}"
                else:
                    example_cmd = "create namespace <name>"

            if st.button("Use in terminal", use_container_width=True, key="k8s_term_builder_apply") and example_cmd:
                st.session_state.k8s_terminal_last = example_cmd  # type: ignore[assignment]
                st.rerun()

        cmd = st.text_input(
            "kubectl-style command",
            value=st.session_state.get("k8s_terminal_last", "get pods -n default"),
            key="k8s_terminal_input",
            help="Executes via MCP tool: kubectl_like",
        )
        col_run, col_examples = st.columns([1, 3])
        with col_run:
            run_clicked = st.button("Run command", type="primary", use_container_width=True, key="k8s_term_run")
        with col_examples:
            st.caption(
                "Examples: `get deployments -A -o yaml` · `get pods -n default` · `get nodes` · `get services -A` · `get events -A` · "
                "`logs my-pod -n default --tail=100` · `scale deployment my-app -n default --replicas=3` · `create namespace staging`"
            )

        if run_clicked and cmd.strip():
            st.session_state.k8s_terminal_last = cmd.strip()  # type: ignore[assignment]
            try:
                captured: Dict[str, Any] = {}

                def _run_stream():
                    yield "Executing via MCP…\n"
                    result = _invoke_tool(tools, "kubectl_like", {"command": cmd.strip()})
                    captured["result"] = result

                    if not isinstance(result, dict):
                        yield "(Unexpected response type)\n"
                        return

                    if not result.get("ok"):
                        yield f"Error: {result.get('error') or 'Command failed'}\n"
                        return

                    text = result.get("text") or ""
                    if text:
                        yield from _stream_text(text)
                    else:
                        yield "(No text output; see Table/Raw tabs above.)\n"

                st.markdown("#### Live output")
                st.write_stream(_run_stream())

                result = captured.get("result")
                ok = bool(isinstance(result, dict) and result.get("ok"))
                error_text = result.get("error") if isinstance(result, dict) else ""
                table_key, table_rows = _extract_table_payload(result)

                text = result.get("text") if isinstance(result, dict) else ""
                output = (result.get("output") if isinstance(result, dict) else None) or "table"
                text_lang = "yaml" if output in ("yaml", "yml") else ("json" if output == "json" else "text")

                entry = {
                    "command": cmd.strip(),
                    "ok": ok,
                    "error": error_text or "",
                    "raw": result,
                    "table_key": table_key,
                    "table_rows": table_rows,
                    "text": text or "",
                    "text_lang": text_lang,
                }
                st.session_state.k8s_terminal_history.append(entry)  # type: ignore[call-arg]
            except Exception as exc:  # noqa: BLE001
                st.session_state.k8s_terminal_history.append(  # type: ignore[call-arg]
                    {"command": cmd.strip(), "ok": False, "error": f"Error executing command: {exc}", "raw": None, "table_key": None, "table_rows": None, "text": "", "text_lang": "text"}
                )
                st.exception(exc)

    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
