from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.streamlit_config import StreamlitAppConfig
from src.theme import set_theme


set_theme(page_title="Setup", page_icon="🛠️")

st.title("Setup")
st.caption("Deploy + debug the deploy/helm/best-streamlit-website chart. This page monitors whether your release is deployed and healthy.")

ROOT = Path(__file__).resolve().parent.parent
CHART_DIR = ROOT / "deploy" / "helm" / "best-streamlit-website"
VALUES_PATH = CHART_DIR / "values.yaml"
CHART_PATH = CHART_DIR / "Chart.yaml"


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"<failed to read {path}: {exc}>"


def _load_yaml(text: str) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _matches(tool_name: str, desired: str) -> bool:
    if tool_name == desired:
        return True
    for sep in ("__", ".", ":"):
        if sep in tool_name and tool_name.rsplit(sep, 1)[-1] == desired:
            return True
    if tool_name.endswith("_" + desired):
        return True
    return False


def _invoke(tools: List[Any], name: str, args: Dict[str, Any]) -> Any:
    tool = next((t for t in tools if _matches(str(getattr(t, "name", "")), name)), None)
    if tool is None:
        available = sorted({str(getattr(t, "name", "")) for t in (tools or []) if getattr(t, "name", None)})
        raise ValueError(f"Tool {name} not found. Available: {available}")

    if hasattr(tool, "ainvoke"):
        return asyncio.run(tool.ainvoke(args))
    return tool.invoke(args)


def _tool_names(tools: List[Any]) -> List[str]:
    return sorted({str(getattr(t, "name", "")) for t in (tools or []) if getattr(t, "name", None)})


def _get_tools(*, cache_key: str, server_name: str, module: str, transport: str, url: str, env: Dict[str, str], force_reload: bool = False) -> List[Any]:
    sig = json.dumps({"transport": transport, "url": url, "env": env}, sort_keys=True)
    sig_key = f"{cache_key}_sig"

    if force_reload or st.session_state.get(sig_key) != sig or cache_key not in st.session_state:
        if transport == "stdio":
            conn = {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", module],
                "env": {**os.environ, **env},
            }
        else:
            conn = {"transport": "sse", "url": url}

        client = MultiServerMCPClient(connections={server_name: conn})
        st.session_state[cache_key] = asyncio.run(client.get_tools())
        st.session_state[sig_key] = sig

    return list(st.session_state.get(cache_key) or [])


def _expected_components(values: Dict[str, Any]) -> Dict[str, bool]:
    comps = values.get("components") if isinstance(values.get("components"), dict) else {}
    def _b(k: str, default: bool = True) -> bool:
        v = comps.get(k, default)
        return bool(v)

    return {
        "streamlit": _b("streamlit", True),
        "mcpJenkins": _b("mcpJenkins", True),
        "mcpKubernetes": _b("mcpKubernetes", True),
        "mcpHelm": _b("mcpHelm", True),
        "agentDataGen": _b("agentDataGen", True),
        "agentJenkins": _b("agentJenkins", True),
        "agentKubernetes": _b("agentKubernetes", True),
    }


def _status_badge(ok: bool, label: str, *, detail: str = "") -> None:
    if ok:
        st.success(label)
    else:
        st.error(label)
    if detail:
        st.caption(detail)


values_text = _safe_read(VALUES_PATH)
chart_text = _safe_read(CHART_PATH)
values = _load_yaml(values_text)
chart = _load_yaml(chart_text)

# Targets
with st.sidebar:
    st.header("Target")
    default_ns = str(values.get("namespace") or "best-streamlit-website")
    namespace = st.text_input("Namespace", value=default_ns)
    release_name = st.text_input("Release name", value="bsw")

    st.markdown("---")
    st.subheader("Connections")
    cfg = StreamlitAppConfig.from_env()
    st.caption("These come from your STREAMLIT_* env vars.")
    st.write(f"Kubernetes MCP: {cfg.kubernetes.mcp_transport} | {cfg.kubernetes.mcp_url}")
    st.write(f"Helm MCP: {cfg.helm.mcp_transport} | {cfg.helm.mcp_url}")

    if st.button("Refresh everything", use_container_width=True):
        for k in (
            "_setup_k8s_tools",
            "_setup_helm_tools",
            "_setup_k8s_tools_sig",
            "_setup_helm_tools_sig",
            "_setup_cache",
        ):
            st.session_state.pop(k, None)
        st.rerun()


# Load tools
k8s_transport = (cfg.kubernetes.mcp_transport or "stdio").lower().strip()
k8s_transport = "sse" if k8s_transport == "http" else k8s_transport
helm_transport = (cfg.helm.mcp_transport or "stdio").lower().strip()
helm_transport = "sse" if helm_transport == "http" else helm_transport

k8s_tools: List[Any] = []
helm_tools: List[Any] = []

c1, c2 = st.columns(2)
with c1:
    st.subheader("Kubernetes MCP")
    try:
        k8s_tools = _get_tools(
            cache_key="_setup_k8s_tools",
            server_name="kubernetes",
            module="src.ai.mcp_servers.kubernetes.mcp",
            transport=k8s_transport,
            url=cfg.kubernetes.mcp_url,
            env=cfg.kubernetes.to_env_overrides(),
        )
        _status_badge(True, f"Loaded {len(k8s_tools)} tools")
    except Exception as exc:  # noqa: BLE001
        _status_badge(False, "Failed to load Kubernetes tools", detail=str(exc))

    with st.expander("Show loaded tool names", expanded=False):
        st.code("\n".join(_tool_names(k8s_tools)) or "<none>", language="text")

with c2:
    st.subheader("Helm MCP")
    try:
        helm_tools = _get_tools(
            cache_key="_setup_helm_tools",
            server_name="helm",
            module="src.ai.mcp_servers.helm.mcp",
            transport=helm_transport,
            url=cfg.helm.mcp_url,
            env=cfg.helm.to_env_overrides(),
        )
        _status_badge(True, f"Loaded {len(helm_tools)} tools")
    except Exception as exc:  # noqa: BLE001
        _status_badge(False, "Failed to load Helm tools", detail=str(exc))

    with st.expander("Show loaded tool names", expanded=False):
        st.code("\n".join(_tool_names(helm_tools)) or "<none>", language="text")

st.markdown("---")


def _cache_get(key: str) -> Any:
    return (st.session_state.get("_setup_cache") or {}).get(key)


def _cache_set(key: str, value: Any) -> None:
    if "_setup_cache" not in st.session_state or not isinstance(st.session_state.get("_setup_cache"), dict):
        st.session_state["_setup_cache"] = {}
    st.session_state["_setup_cache"][key] = value


def _run_checks() -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True}

    if k8s_tools:
        out["k8s_health"] = _invoke(k8s_tools, "health_check", {})
    else:
        out["k8s_health"] = {"ok": False, "error": "Kubernetes tools not loaded"}

    if helm_tools:
        out["helm_health"] = _invoke(helm_tools, "health_check", {})
    else:
        out["helm_health"] = {"ok": False, "error": "Helm tools not loaded"}

    # Cluster objects (namespace scoped)
    if k8s_tools:
        out["namespaces"] = _invoke(k8s_tools, "list_namespaces", {})
        out["deployments"] = _invoke(k8s_tools, "list_deployments", {"namespace": namespace})
        out["pods"] = _invoke(k8s_tools, "list_pods", {"namespace": namespace})
        out["services"] = _invoke(k8s_tools, "list_services", {"namespace": namespace})
        out["events"] = _invoke(k8s_tools, "list_events", {"namespace": namespace, "limit": 200})
        out["service_accounts"] = _invoke(k8s_tools, "list_service_accounts", {"namespace": namespace})

    # Helm releases
    if helm_tools:
        out["releases"] = _invoke(helm_tools, "list_releases", {"all_namespaces": True})

    return out


if st.button("Refresh status", use_container_width=True):
    _cache_set("status", _run_checks())

status = _cache_get("status")
if not isinstance(status, dict):
    status = _run_checks()
    _cache_set("status", status)


def _as_list(obj: Any, key: str) -> List[Dict[str, Any]]:
    if not isinstance(obj, dict) or not obj.get("ok"):
        return []
    val = obj.get(key)
    return val if isinstance(val, list) else []


def _ready_ok(ready: Any) -> bool:
    """Return True when ready is like '2/2'."""
    if not isinstance(ready, str) or "/" not in ready:
        return False
    left, right = ready.split("/", 1)
    try:
        return int(left) == int(right)
    except Exception:
        return False


# Overview KPIs
releases = _as_list(status.get("releases"), "releases")
rel_match = [r for r in releases if str(r.get("name")) == str(release_name)]
rel_ns_match = [r for r in rel_match if (not namespace) or str(r.get("namespace")) == str(namespace)]

pods = _as_list(status.get("pods"), "pods")
deployments = _as_list(status.get("deployments"), "deployments")
services = _as_list(status.get("services"), "services")
events = _as_list(status.get("events"), "events")
service_accounts = _as_list(status.get("service_accounts"), "service_accounts")

bad_deployments = [d for d in deployments if (d.get("readyReplicas") or 0) < (d.get("replicas") or 0)]
non_running_pods = [p for p in pods if str(p.get("phase")) != "Running" or not _ready_ok(p.get("ready"))]
warning_events = [e for e in events if str(e.get("type")) == "Warning"]

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
with kpi1:
    st.metric("Release present", "yes" if rel_match else "no")
with kpi2:
    st.metric("Deployments", str(len(deployments)))
with kpi3:
    st.metric("Not-ready deployments", str(len(bad_deployments)))
with kpi4:
    st.metric("Warning events", str(len(warning_events)))

st.markdown("---")

# Health details
st.subheader("Connectivity")
ch1, ch2 = st.columns(2)
with ch1:
    st.markdown("#### Kubernetes")
    st.json(status.get("k8s_health"))
with ch2:
    st.markdown("#### Helm")
    st.json(status.get("helm_health"))

st.markdown("---")

# Helm release
st.subheader("Helm release")
if not helm_tools:
    st.warning("Helm MCP tools not available; cannot query releases.")
else:
    if not releases:
        st.warning("No releases found (or list_releases failed).")
        st.json(status.get("releases"))
    else:
        st.write("All releases (filtered to this target below):")
        view = rel_match if rel_match else releases
        st.dataframe(view, use_container_width=True, hide_index=True)

    if rel_ns_match:
        picked = rel_ns_match[0]
        rel_ns = str(picked.get("namespace") or namespace)
        st.success(f"Found release '{release_name}' in namespace '{rel_ns}'.")
        try:
            rel_status = _invoke(helm_tools, "get_release_status", {"release": release_name, "namespace": rel_ns})
        except Exception as exc:  # noqa: BLE001
            rel_status = {"ok": False, "error": str(exc)}
        st.markdown("##### get_release_status")
        st.json(rel_status)
    else:
        st.error(f"Release '{release_name}' not found in namespace '{namespace}'.")
        st.info(
            "Install/upgrade from your machine:\n"
            f"helm upgrade --install {release_name} deploy/helm/best-streamlit-website -n {namespace} --create-namespace -f deploy/helm/best-streamlit-website/values.yaml"
        )

st.markdown("---")

# Kubernetes resources
st.subheader("Kubernetes resources")

if not k8s_tools:
    st.warning("Kubernetes MCP tools not available; cannot query cluster resources.")
else:
    st.markdown("#### Deployments")
    if deployments:
        st.dataframe(deployments, use_container_width=True, hide_index=True)
    else:
        st.info("No deployments returned.")
        st.json(status.get("deployments"))

    if bad_deployments:
        st.error("Some deployments are not ready")
        st.dataframe(bad_deployments, use_container_width=True, hide_index=True)

    st.markdown("#### Pods")
    if pods:
        st.dataframe(pods, use_container_width=True, hide_index=True)
    else:
        st.info("No pods returned.")
        st.json(status.get("pods"))

    if non_running_pods:
        st.warning("Some pods are not healthy (phase/ready)")
        st.dataframe(non_running_pods, use_container_width=True, hide_index=True)

    st.markdown("#### Services")
    if services:
        st.dataframe(services, use_container_width=True, hide_index=True)
    else:
        st.info("No services returned.")
        st.json(status.get("services"))

    st.markdown("#### Events")
    if warning_events:
        st.error("Warning events detected")
        st.dataframe(warning_events[:80], use_container_width=True, hide_index=True)
    elif events:
        st.info("No warning events in the last 200 events.")
        st.dataframe(events[:80], use_container_width=True, hide_index=True)
    else:
        st.info("No events returned.")
        st.json(status.get("events"))

st.markdown("---")

# Pod logs
st.subheader("Pod logs")
if k8s_tools and pods:
    pod_options = [f"{p.get('namespace')}/{p.get('name')}" for p in pods if p.get("name") and p.get("namespace")]
    picked_pod = st.selectbox("Pod", options=[""] + pod_options, index=0)
    tail = st.number_input("Tail lines", min_value=10, max_value=5000, value=200, step=50)
    if picked_pod and st.button("Fetch logs", use_container_width=True):
        ns, name = picked_pod.split("/", 1)
        st.json(_invoke(k8s_tools, "get_pod_logs", {"name": name, "namespace": ns, "tail_lines": int(tail)}))
else:
    st.info("Load Kubernetes tools and ensure pods exist to view logs.")

st.markdown("---")

# Checklist
st.subheader("Deployment checklist")

expected = _expected_components(values)
expected_deployments = {
    "streamlit": "streamlit-app",
    "mcpJenkins": "jenkins-mcp",
    "mcpKubernetes": "kubernetes-mcp",
    "mcpHelm": "helm-mcp",
    "agentDataGen": "datagen-agent",
    "agentJenkins": "jenkins-agent",
    "agentKubernetes": "kubernetes-agent",
}
expected_services = {
    "streamlit": "streamlit-app",
    "mcpJenkins": "jenkins-mcp",
    "mcpKubernetes": "kubernetes-mcp",
    "mcpHelm": "helm-mcp",
    "agentDataGen": "datagen-agent",
    "agentJenkins": "jenkins-agent",
    "agentKubernetes": "kubernetes-agent",
}

ns_list = _as_list(status.get("namespaces"), "namespaces")
ns_exists = any(str(n.get("name")) == str(namespace) for n in ns_list)

svc_names = {str(s.get("name")) for s in services}
dep_names = {str(d.get("name")) for d in deployments}

st.markdown("##### Basic")
_status_badge(ns_exists, f"Namespace exists: {namespace}")
_status_badge(bool(rel_match), f"Helm release exists: {release_name}")

st.markdown("##### Chart components")
for comp_key, enabled in expected.items():
    dep = expected_deployments.get(comp_key)
    svc = expected_services.get(comp_key)

    if not enabled:
        st.info(f"{comp_key}: disabled in values.yaml")
        continue

    dep_ok = bool(dep and dep in dep_names)
    svc_ok = bool(svc and svc in svc_names)

    if dep:
        _status_badge(dep_ok, f"Deployment present: {dep}")
    if svc:
        _status_badge(svc_ok, f"Service present: {svc}")

st.markdown("##### Helm MCP RBAC")
if expected.get("mcpHelm"):
    sa_ok = any(str(sa.get("name")) == "helm-mcp" for sa in service_accounts)
    _status_badge(sa_ok, "ServiceAccount present: helm-mcp", detail="If rbac.helmMcp.enabled=true, helm-mcp should run with this ServiceAccount.")

st.markdown("---")

with st.expander("Chart + values (local files)", expanded=False):
    st.markdown("##### Chart.yaml")
    st.code(chart_text, language="yaml")
    st.markdown("##### values.yaml")
    st.code(values_text, language="yaml")

with st.expander("Useful debug commands", expanded=False):
    st.code(
        "\n".join(
            [
                f"helm list -A | findstr {release_name}",
                f"helm status {release_name} -n {namespace}",
                f"kubectl get all -n {namespace}",
                f"kubectl get events -n {namespace} --sort-by=.lastTimestamp | tail -n 50",
                f"kubectl describe deploy/streamlit-app -n {namespace}",
                f"kubectl logs deploy/streamlit-app -n {namespace} --tail=200",
            ]
        ),
        language="bash",
    )
