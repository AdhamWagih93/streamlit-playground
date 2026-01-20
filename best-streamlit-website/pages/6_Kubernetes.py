import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import os

import plotly.express as px
import streamlit as st
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.theme import set_theme


PAGE_TITLE = "Kubernetes"


def _get_server_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "src" / "ai" / "mcp_servers" / "kubernetes_server.py")


def _build_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    kubeconfig = st.session_state.get("k8s_kubeconfig")
    context = st.session_state.get("k8s_context")
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
        server_path = _get_server_path()
        client = MultiServerMCPClient(
            connections={
                "kubernetes": {
                    "transport": "stdio",
                    "command": "python",
                    "args": [server_path],
                    "env": env,
                }
            }
        )
        st.session_state["_k8s_tools"] = asyncio.run(client.get_tools())
        st.session_state["_k8s_tools_sig"] = sig
    return st.session_state["_k8s_tools"]


def _invoke_tool(tools, name: str, args: Dict[str, Any]) -> Any:
    tool = next((t for t in tools if getattr(t, "name", "") == name), None)
    if tool is None:
        raise ValueError(f"Tool {name} not found")

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
    for key in ("pods", "nodes", "namespaces", "deployments", "services", "events"):
        if key in payload and isinstance(payload[key], list):
            return key, payload[key]
    return None, None


def main() -> None:
    set_theme(PAGE_TITLE)

    # Page-level styling for a richer Kubernetes dashboard
    st.markdown(
        """
        <style>
        .k8s-layout { max-width: 1200px; margin: 0 auto; }
        .k8s-hero {
            background: linear-gradient(120deg, #0b63d6, #22c55e, #0ea5e9);
            border-radius: 18px;
            padding: 1.7rem 1.6rem 1.4rem 1.6rem;
            margin-bottom: 1.2rem;
            color: #fff;
            box-shadow: 0 12px 32px rgba(15, 23, 42, 0.35);
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
            reload_tools = st.button("Reload tools", type="primary", use_container_width=True)
        with col_b:
            refresh = st.button("Refresh data", use_container_width=True)

        st.caption("All Kubernetes interactions are executed through the MCP server.")

    env = _build_env()

    try:
        tools = _get_tools(env, force_reload=reload_tools)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load Kubernetes MCP tools: {exc}")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    # Snapshot (single refresh point to avoid repeated MCP calls during one render)
    if refresh or "k8s_snapshot" not in st.session_state:
        with st.spinner("Fetching cluster data via MCP…"):
            snapshot: Dict[str, Any] = {}
            snapshot["overview"] = _invoke_tool(tools, "get_cluster_overview", {})
            snapshot["namespaces"] = _invoke_tool(tools, "list_namespaces", {})
            snapshot["nodes"] = _invoke_tool(tools, "list_nodes", {})
            snapshot["pods"] = _invoke_tool(tools, "list_pods", {})
            snapshot["deployments"] = _invoke_tool(tools, "list_deployments_all", {})
            snapshot["services"] = _invoke_tool(tools, "list_services_all", {})
            snapshot["events"] = _invoke_tool(tools, "list_events_all", {"limit": 250})
            st.session_state.k8s_snapshot = snapshot

    snapshot = st.session_state.get("k8s_snapshot", {})

    overview = snapshot.get("overview")
    namespaces = _as_list(snapshot.get("namespaces"), "namespaces")
    nodes = _as_list(snapshot.get("nodes"), "nodes")
    pods = _as_list(snapshot.get("pods"), "pods")
    deployments = _as_list(snapshot.get("deployments"), "deployments")
    services = _as_list(snapshot.get("services"), "services")
    events = _as_list(snapshot.get("events"), "events")

    st.markdown("<div class='k8s-card'>", unsafe_allow_html=True)

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown("<div class='k8s-section-title'>Cluster Summary</div>", unsafe_allow_html=True)

        if isinstance(overview, dict) and overview.get("ok"):
            pods_metrics = _pod_phase_metrics(pods)
            dep_metrics = _deployment_health(deployments)

            mcol1, mcol2, mcol3, mcol4 = st.columns(4)
            mcol1.metric("Nodes", len(nodes))
            mcol2.metric("Namespaces", len(namespaces))
            mcol3.metric("Pods", pods_metrics.get("total", 0))
            mcol4.metric("Deployments", len(deployments))

            st.markdown(
                """
                <span class='k8s-pill'>Running</span>
                <span class='k8s-pill'>Pending</span>
                <span class='k8s-pill'>Failed</span>
                <span class='k8s-pill'>Services</span>
                """,
                unsafe_allow_html=True,
            )

            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Running pods", pods_metrics["running"])
            s2.metric("Pending pods", pods_metrics["pending"])
            s3.metric("Failed pods", pods_metrics["failed"])
            s4.metric("Services", len(services))

            st.caption(f"Deployment readiness: {dep_metrics['ready']}/{dep_metrics['desired']} ready ({dep_metrics['pct']:.1f}%)")
        else:
            _render_tool_error("Cluster overview failed", overview)

    with col2:
        st.markdown("<div class='k8s-section-title'>Quick Links</div>", unsafe_allow_html=True)
        st.write("Terminal is available as a separate page: **Kubernetes Terminal**.")
        st.write("Use the tabs below for dashboards, explorer views, and actions.")

    tabs = st.tabs([
        "Overview",
        "Namespaces",
        "Nodes",
        "Workloads",
        "Pods",
        "Services",
        "Events",
        "Actions",
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
            st.plotly_chart(fig, use_container_width=True)

            top_ns = _top_counts(pods, "namespace", top_n=12)
            if top_ns:
                fig2 = px.bar(
                    x=[k for k, _ in top_ns],
                    y=[v for _, v in top_ns],
                    labels={"x": "Namespace", "y": "Pods"},
                    title="Top namespaces by pod count",
                )
                st.plotly_chart(fig2, use_container_width=True)
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
            fig3 = px.histogram(
                rows,
                x="namespace",
                y="unready",
                title="Unready replicas by namespace (deployments)",
            )
            st.plotly_chart(fig3, use_container_width=True)

    # Namespaces
    with tabs[1]:
        st.subheader("Namespaces")
        if not namespaces:
            st.info("No namespaces returned.")
        else:
            st.dataframe(namespaces, use_container_width=True, height=360)
            status_counts = _count_by(namespaces, "status")
            fig = px.bar(
                x=list(status_counts.keys()),
                y=list(status_counts.values()),
                labels={"x": "Status", "y": "Count"},
                title="Namespaces by Status",
            )
            st.plotly_chart(fig, use_container_width=True)

    # Nodes
    with tabs[2]:
        st.subheader("Nodes")
        if not nodes:
            st.info("No nodes returned.")
        else:
            st.dataframe(nodes, use_container_width=True, height=360)
            ready_counts: Dict[str, int] = {"Ready": 0, "NotReady": 0}
            for node in nodes:
                ready = False
                for cond in node.get("conditions", []):
                    if cond.get("type") == "Ready" and cond.get("status") == "True":
                        ready = True
                        break
                ready_counts["Ready" if ready else "NotReady"] += 1
            fig = px.pie(names=list(ready_counts.keys()), values=list(ready_counts.values()), title="Node Readiness")
            st.plotly_chart(fig, use_container_width=True)

    # Workloads
    with tabs[3]:
        st.subheader("Deployments")
        if not deployments:
            st.info("No deployments returned.")
        else:
            ns_options = sorted({d.get("namespace") for d in deployments if d.get("namespace")})
            selected_ns = st.selectbox("Namespace", options=["(all)"] + ns_options)
            view = deployments if selected_ns == "(all)" else [d for d in deployments if d.get("namespace") == selected_ns]
            st.dataframe(view, use_container_width=True, height=360)

            health = _deployment_health(view)
            st.caption(f"Ready replicas: {health['ready']}/{health['desired']} ({health['pct']:.1f}%)")

    # Pods
    with tabs[4]:
        st.subheader("Pods Explorer")
        if not pods:
            st.info("No pods returned.")
        else:
            ns_options = sorted({p.get("namespace") for p in pods if p.get("namespace")})
            phase_options = sorted({p.get("phase") for p in pods if p.get("phase")})
            col_a, col_b = st.columns(2)
            with col_a:
                selected_ns = st.selectbox("Namespace", options=["(all)"] + ns_options)
            with col_b:
                selected_phase = st.selectbox("Phase", options=["(all)"] + phase_options)

            view = pods
            if selected_ns != "(all)":
                view = [p for p in view if p.get("namespace") == selected_ns]
            if selected_phase != "(all)":
                view = [p for p in view if p.get("phase") == selected_phase]

            st.dataframe(view, use_container_width=True, height=360)

            st.markdown("---")
            st.subheader("Pod Logs")
            # Prefer selecting from known pods to avoid typos
            pod_choices = [f"{p.get('namespace')}/{p.get('name')}" for p in view if p.get("name") and p.get("namespace")]
            pick = st.selectbox("Pod", options=[""] + sorted(pod_choices))
            tail_lines = st.number_input("Tail lines", min_value=10, max_value=2000, value=200, step=10)
            if st.button("Fetch logs"):
                if not pick:
                    st.warning("Select a pod first.")
                else:
                    ns, name = pick.split("/", 1)
                    log_result = _invoke_tool(tools, "get_pod_logs", {"name": name, "namespace": ns, "tail_lines": int(tail_lines)})
                    if isinstance(log_result, dict) and log_result.get("ok"):
                        st.text_area("Logs", value=log_result.get("logs", ""), height=360)
                    else:
                        _render_tool_error("Fetching logs failed", log_result)

    # Services
    with tabs[5]:
        st.subheader("Services")
        if not services:
            st.info("No services returned.")
        else:
            st.dataframe(services, use_container_width=True, height=360)
            type_counts = _count_by(services, "type")
            fig = px.bar(
                x=list(type_counts.keys()),
                y=list(type_counts.values()),
                labels={"x": "Type", "y": "Services"},
                title="Services by Type",
            )
            st.plotly_chart(fig, use_container_width=True)

    # Events
    with tabs[6]:
        st.subheader("Events")
        if not events:
            st.info("No events returned.")
        else:
            ns_options = sorted({e.get("namespace") for e in events if e.get("namespace")})
            selected_ns = st.selectbox("Namespace", options=["(all)"] + ns_options, key="events_ns")
            view = events if selected_ns == "(all)" else [e for e in events if e.get("namespace") == selected_ns]
            st.dataframe(view, use_container_width=True, height=360)
            top_reasons = _top_counts(view, "reason", top_n=12)
            if top_reasons:
                fig = px.bar(
                    x=[k for k, _ in top_reasons],
                    y=[v for _, v in top_reasons],
                    labels={"x": "Reason", "y": "Count"},
                    title="Top event reasons",
                )
                st.plotly_chart(fig, use_container_width=True)

    # Actions
    with tabs[7]:
        st.subheader("Safe Actions")
        st.write("These actions affect the cluster. They execute via MCP.")

        act_tab_scale, act_tab_restart, act_tab_delete = st.tabs(["Scale deployment", "Restart deployment", "Delete pod"])

        with act_tab_scale:
            dep_ns = st.text_input("Namespace", value="default", key="scale_ns")
            dep_name = st.text_input("Deployment name", key="scale_name")
            dep_replicas = st.number_input("Replicas", min_value=0, max_value=1000, value=1, step=1)
            confirm = st.checkbox("I understand this changes live workload", key="scale_confirm")
            if st.button("Scale", type="primary"):
                if not (dep_name and confirm):
                    st.warning("Provide a deployment name and confirm.")
                else:
                    scale_result = _invoke_tool(tools, "scale_deployment", {"name": dep_name, "namespace": dep_ns, "replicas": int(dep_replicas)})
                    if isinstance(scale_result, dict) and scale_result.get("ok"):
                        st.success(f"Scaled {scale_result.get('name')} in {scale_result.get('namespace')} to {scale_result.get('replicas')} replicas.")
                    else:
                        _render_tool_error("Scaling failed", scale_result)

        with act_tab_restart:
            dep_ns = st.text_input("Namespace", value="default", key="restart_ns")
            dep_name = st.text_input("Deployment name", key="restart_name")
            confirm = st.checkbox("I understand this will restart pods", key="restart_confirm")
            if st.button("Restart deployment", type="secondary"):
                if not (dep_name and confirm):
                    st.warning("Provide a deployment name and confirm.")
                else:
                    restart_result = _invoke_tool(tools, "restart_deployment", {"name": dep_name, "namespace": dep_ns})
                    if isinstance(restart_result, dict) and restart_result.get("ok"):
                        st.success(f"Restart triggered for {restart_result.get('name')} in {restart_result.get('namespace')}.")
                    else:
                        _render_tool_error("Restart failed", restart_result)

        with act_tab_delete:
            del_ns = st.text_input("Namespace", value="default", key="delete_ns")
            del_name = st.text_input("Pod name", key="delete_name")
            confirm = st.checkbox("I understand this deletes the pod", key="delete_confirm")
            if st.button("Delete pod", type="secondary"):
                if not (del_name and confirm):
                    st.warning("Provide a pod name and confirm.")
                else:
                    del_result = _invoke_tool(tools, "delete_pod", {"name": del_name, "namespace": del_ns})
                    if isinstance(del_result, dict) and del_result.get("ok"):
                        st.success(f"Deleted pod {del_result.get('name')} in {del_result.get('namespace')}.")
                    else:
                        _render_tool_error("Delete failed", del_result)

    # Terminal
    with tabs[8]:
        st.subheader("Kubernetes Terminal")
        st.caption("A kubectl-style console powered by the Kubernetes MCP server (no direct kubectl execution).")

        if "k8s_terminal_history" not in st.session_state:
            st.session_state.k8s_terminal_history = []  # type: ignore[assignment]

        col_a, col_b = st.columns([1, 1])
        with col_a:
            if st.button("Clear terminal history", use_container_width=True):
                st.session_state.k8s_terminal_history = []  # type: ignore[assignment]
        with col_b:
            st.caption("Supported: get pods/nodes/ns/deployments/services/events · logs · delete pod · scale deployment")

        history = st.session_state.k8s_terminal_history  # type: ignore[assignment]
        with st.container():
            if history:
                last = history[-1]
                st.markdown("#### Last command")
                st.code(last.get("command", ""), language="bash")
                if last.get("ok") and last.get("table_rows") is not None:
                    st.caption(f"Table view: {last.get('table_key')} ({len(last.get('table_rows') or [])} rows)")
                    st.dataframe(last.get("table_rows"), use_container_width=True, height=300)
                elif not last.get("ok"):
                    st.error(last.get("error") or "Command failed")

                with st.expander("Raw MCP response", expanded=False):
                    st.json(last.get("raw"))

        st.markdown("---")
        cmd = st.text_input(
            "kubectl-style command",
            value=st.session_state.get("k8s_terminal_last", "get pods -n default"),
            key="k8s_terminal_input",
            help="Executes via MCP tool: kubectl_like",
        )
        col_run, col_examples = st.columns([1, 3])
        with col_run:
            run_clicked = st.button("Run command", type="primary", use_container_width=True)
        with col_examples:
            st.caption(
                "Examples: `get pods -n default` · `get nodes` · `get services -n default` · `get events -n default` · "
                "`logs my-pod -n default --tail=100` · `scale deployment my-app -n default --replicas=3`"
            )

        if run_clicked and cmd.strip():
            st.session_state.k8s_terminal_last = cmd.strip()  # type: ignore[assignment]
            with st.spinner("Executing via MCP…"):
                try:
                    result = _invoke_tool(tools, "kubectl_like", {"command": cmd.strip()})
                    ok = bool(isinstance(result, dict) and result.get("ok"))
                    error_text = result.get("error") if isinstance(result, dict) else ""
                    table_key, table_rows = _extract_table_payload(result)

                    st.session_state.k8s_terminal_history.append(  # type: ignore[call-arg]
                        {
                            "command": cmd.strip(),
                            "ok": ok,
                            "error": error_text or "",
                            "raw": result,
                            "table_key": table_key,
                            "table_rows": table_rows,
                        }
                    )
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.session_state.k8s_terminal_history.append(  # type: ignore[call-arg]
                        {"command": cmd.strip(), "ok": False, "error": f"Error executing command: {exc}", "raw": None}
                    )
                    st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
