from __future__ import annotations

import asyncio
import json
import os
import sys
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.ai.mcp_langchain_tools import invoke_tool as _invoke_shared
from src.ai.mcp_langchain_tools import matches_tool_name as _matches_shared
from src.ai.mcp_langchain_tools import normalise_mcp_result as _normalise_shared
from src.streamlit_config import StreamlitAppConfig
from src.theme import set_theme


set_theme(page_title="Setup", page_icon="🛠️")

st.title("Setup")
st.caption(
    "Deploy using either Helm or raw Kubernetes manifests. "
    "Both flows start by building/pushing images (Docker MCP) and verifying them in Nexus (Nexus MCP)."
)

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
    return _matches_shared(tool_name, desired)


def _normalise_mcp_result(value: Any) -> Any:
    return _normalise_shared(value)


def _invoke(tools: List[Any], name: str, args: Dict[str, Any]) -> Any:
    return _invoke_shared(list(tools or []), name, dict(args or {}))


def _tool_names(tools: List[Any]) -> List[str]:
    return sorted({str(getattr(t, "name", "")) for t in (tools or []) if getattr(t, "name", None)})


def _render_jsonish(value: Any) -> None:
    """Render a value in the UI without triggering Streamlit's JSON viewer errors."""

    if isinstance(value, (dict, list)):
        st.json(value)
        return

    if value is None:
        st.caption("<no data>")
        return

    # Streamlit's JSON viewer expects an object/array; show scalars as text.
    st.code(str(value), language="text")


def _get_tools(
    *,
    cache_key: str,
    server_name: str,
    module: str,
    transport: str,
    url: str,
    env: Dict[str, str],
    python_executable: Optional[str] = None,
    force_reload: bool = False,
    watched_files: Optional[List[Path]] = None,
) -> List[Any]:
    # Convert "http" to "sse" for MCP over HTTP
    transport = transport.lower().strip()
    if transport == "http":
        transport = "sse"

    # Include code mtime so local edits to the MCP server force reload.
    try:
        watched = list(watched_files or [])
        code_mtime = 0
        for p in watched:
            if p.is_file():
                code_mtime = max(code_mtime, int(p.stat().st_mtime_ns))
    except Exception:  # noqa: BLE001
        code_mtime = 0

    sig = json.dumps(
        {
            "transport": transport,
            "url": url,
            "env": env,
            "code_mtime": code_mtime,
            "python": (python_executable or sys.executable),
        },
        sort_keys=True,
    )
    sig_key = f"{cache_key}_sig"

    if force_reload or st.session_state.get(sig_key) != sig or cache_key not in st.session_state:
        subprocess_env = {**os.environ, **env}

        # Ensure the stdio subprocess imports this workspace's `src/...` package.
        repo_root = str(ROOT)
        existing_pp = subprocess_env.get("PYTHONPATH", "")
        if existing_pp:
            subprocess_env["PYTHONPATH"] = repo_root + os.pathsep + existing_pp
        else:
            subprocess_env["PYTHONPATH"] = repo_root

        if transport == "stdio":
            conn = {
                "transport": "stdio",
                "command": (python_executable or sys.executable),
                "args": ["-m", module],
                "env": subprocess_env,
            }
        else:
            conn = {"transport": transport, "url": url}

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


def _cache_get(key: str) -> Any:
    return (st.session_state.get("_setup_cache") or {}).get(key)


def _cache_set(key: str, value: Any) -> None:
    if "_setup_cache" not in st.session_state or not isinstance(st.session_state.get("_setup_cache"), dict):
        st.session_state["_setup_cache"] = {}
    st.session_state["_setup_cache"][key] = value


from src.streamlit_config import get_app_config

cfg = get_app_config()


def _watched_k8s_files() -> List[Path]:
    return [
        ROOT / "src" / "ai" / "mcp_servers" / "kubernetes" / "mcp.py",
        ROOT / "src" / "ai" / "mcp_servers" / "kubernetes" / "utils" / "helm.py",
        ROOT / "src" / "ai" / "mcp_servers" / "kubernetes" / "utils" / "helm_config.py",
        ROOT / "src" / "ai" / "mcp_servers" / "kubernetes" / "utils" / "helm_cli.py",
        ROOT / "src" / "ai" / "mcp_servers" / "kubernetes" / "utils" / "pyhelm3_backend.py",
    ]


def _watched_docker_files() -> List[Path]:
    return [
        ROOT / "src" / "ai" / "mcp_servers" / "docker" / "mcp.py",
    ]


def _watched_nexus_files() -> List[Path]:
    return [
        ROOT / "src" / "ai" / "mcp_servers" / "nexus" / "mcp.py",
    ]


with st.sidebar:
    st.header("Target")
    default_ns = str(values.get("namespace") or "best-streamlit-website")
    namespace = st.text_input("Namespace", value=default_ns)
    release_name = st.text_input("Release name", value="bsw")

    st.markdown("---")
    st.subheader("Connections")
    st.caption("Env-first: STREAMLIT_* vars control remote MCP URLs/transport.")

    default_force_stdio = platform.system().lower().startswith("win")

    st.checkbox(
        "Force local kubernetes-mcp (stdio)",
        value=bool(st.session_state.get("setup_force_k8s_stdio", default_force_stdio)),
        key="setup_force_k8s_stdio",
        help="Starts kubernetes-mcp as a local stdio subprocess.",
    )
    st.checkbox(
        "Force local docker-mcp (stdio)",
        value=bool(st.session_state.get("setup_force_docker_stdio", default_force_stdio)),
        key="setup_force_docker_stdio",
        help="Starts docker-mcp as a local stdio subprocess.",
    )
    st.checkbox(
        "Force local nexus-mcp (stdio)",
        value=bool(st.session_state.get("setup_force_nexus_stdio", default_force_stdio)),
        key="setup_force_nexus_stdio",
        help="Starts nexus-mcp as a local stdio subprocess.",
    )

    st.markdown("---")
    st.caption(f"Python: {sys.executable}")

    # Pick the Python used to spawn local stdio MCP servers.
    # This can differ from Streamlit's Python and must have deps installed.
    candidates: List[str] = []
    for c in [
        sys.executable,
        shutil.which("python") or "",
        shutil.which("python3") or "",
        shutil.which("python3.11") or "",
    ]:
        c = str(c or "").strip()
        if c and c not in candidates:
            candidates.append(c)

    st.selectbox(
        "Python for local MCP subprocesses",
        options=candidates or [sys.executable],
        index=0,
        key="setup_mcp_python",
        help="This Python must have requirements installed (fastmcp, docker, kubernetes, requests...).",
    )

    if st.button("Clear Setup cache", use_container_width=True):
        for k in [
            "_setup_cache",
            "_setup_k8s_tools",
            "_setup_k8s_tools_sig",
            "_setup_docker_tools",
            "_setup_docker_tools_sig",
            "_setup_nexus_tools",
            "_setup_nexus_tools_sig",
        ]:
            st.session_state.pop(k, None)
        st.rerun()

    def _transport(force_key: str, default_transport: str) -> str:
        return ("stdio" if st.session_state.get(force_key) else (default_transport or "stdio")).lower().strip()

    k8s_transport = _transport("setup_force_k8s_stdio", cfg.kubernetes.mcp_transport)
    docker_transport = _transport("setup_force_docker_stdio", cfg.docker.mcp_transport)
    nexus_transport = _transport("setup_force_nexus_stdio", cfg.nexus.mcp_transport)

    st.markdown("---")
    st.subheader("Kubernetes Context")
    st.caption("Optional overrides used only for the local kubernetes-mcp subprocess.")
    try:
        detected_kc = Path.home() / ".kube" / "config"
        detected_kc_str = str(detected_kc) if detected_kc.is_file() else ""
    except Exception:  # noqa: BLE001
        detected_kc_str = ""

    kubeconfig_override = st.text_input(
        "Kubeconfig path (K8S_KUBECONFIG)",
        value=str(cfg.kubernetes.kubeconfig or detected_kc_str or ""),
        placeholder="C:/Users/<you>/.kube/config",
    )
    context_override = st.text_input(
        "Context (K8S_CONTEXT)",
        value=str(cfg.kubernetes.context or ""),
        placeholder="",
    )

    if st.button("Refresh everything", use_container_width=True):
        for k in (
            "_setup_k8s_tools",
            "_setup_k8s_tools_sig",
            "_setup_docker_tools",
            "_setup_docker_tools_sig",
            "_setup_nexus_tools",
            "_setup_nexus_tools_sig",
            "_setup_cache",
        ):
            st.session_state.pop(k, None)
        st.rerun()


def _k8s_tool_env() -> Dict[str, str]:
    env = dict(cfg.kubernetes.to_env_overrides())
    if kubeconfig_override.strip():
        kc = kubeconfig_override.strip()
        env["K8S_KUBECONFIG"] = kc
        env["KUBECONFIG"] = kc
    if context_override.strip():
        env["K8S_CONTEXT"] = context_override.strip()
    return env


k8s_tools: List[Any] = []
docker_tools: List[Any] = []
nexus_tools: List[Any] = []


_mcp_python = str(st.session_state.get("setup_mcp_python") or sys.executable)


@st.fragment
def _render_mcp_tools_fragment() -> Tuple[List[Any], List[Any], List[Any]]:
    """Render MCP tool loading in an isolated fragment.

    This prevents slow tool discovery (stdio subprocess startup / remote tool listing)
    from re-running on every widget interaction elsewhere on the page.
    """

    st.subheader("MCP tools")
    st.caption("To speed up initial render, tools are loaded on demand.")

    if "setup_autoload_tools" not in st.session_state:
        st.session_state["setup_autoload_tools"] = False

    cols = st.columns([1, 1, 1])
    with cols[0]:
        autoload = st.toggle(
            "Auto-load on open",
            value=bool(st.session_state.get("setup_autoload_tools")),
            key="setup_autoload_tools",
            help="If enabled, this page will connect to MCP servers during initial render.",
        )
    with cols[1]:
        load_now = st.button("Load/refresh tools", use_container_width=True, key="setup_load_tools")
    with cols[2]:
        show_names = st.checkbox("Show tool names", value=False, key="setup_show_tool_names")

    # If we haven't loaded tools yet and autoload is off, don't connect.
    have_any_cached = any(
        k in st.session_state
        for k in ("_setup_k8s_tools", "_setup_docker_tools", "_setup_nexus_tools")
    )
    if not autoload and not load_now and not have_any_cached:
        st.info("Click 'Load/refresh tools' when you're ready to connect.")
        return [], [], []

    k8s_tools_local: List[Any] = []
    docker_tools_local: List[Any] = []
    nexus_tools_local: List[Any] = []

    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown("#### kubernetes-mcp")
        try:
            k8s_tools_local = _get_tools(
                cache_key="_setup_k8s_tools",
                server_name="kubernetes",
                module="src.ai.mcp_servers.kubernetes.mcp",
                transport=k8s_transport,
                url=cfg.kubernetes.mcp_url,
                env=_k8s_tool_env(),
                python_executable=_mcp_python,
                watched_files=_watched_k8s_files(),
                force_reload=bool(load_now),
            )
            _status_badge(True, f"Loaded {len(k8s_tools_local)} tool(s)")
        except Exception as exc:  # noqa: BLE001
            _status_badge(False, "Failed to load tools", detail=str(exc))
        if show_names:
            with st.expander("Tool names", expanded=False):
                st.code("\n".join(_tool_names(k8s_tools_local)) or "<none>", language="text")

    with c2:
        st.markdown("#### docker-mcp")
        try:
            docker_tools_local = _get_tools(
                cache_key="_setup_docker_tools",
                server_name="docker",
                module="src.ai.mcp_servers.docker.mcp",
                transport=docker_transport,
                url=cfg.docker.mcp_url,
                env=dict(cfg.docker.to_env_overrides()),
                python_executable=_mcp_python,
                watched_files=_watched_docker_files(),
                force_reload=bool(load_now),
            )
            _status_badge(True, f"Loaded {len(docker_tools_local)} tool(s)")
        except Exception as exc:  # noqa: BLE001
            _status_badge(False, "Failed to load tools", detail=str(exc))
        if show_names:
            with st.expander("Tool names", expanded=False):
                st.code("\n".join(_tool_names(docker_tools_local)) or "<none>", language="text")

    with c3:
        st.markdown("#### nexus-mcp")
        try:
            nexus_tools_local = _get_tools(
                cache_key="_setup_nexus_tools",
                server_name="nexus",
                module="src.ai.mcp_servers.nexus.mcp",
                transport=nexus_transport,
                url=cfg.nexus.mcp_url,
                env=dict(cfg.nexus.to_env_overrides()),
                python_executable=_mcp_python,
                watched_files=_watched_nexus_files(),
                force_reload=bool(load_now),
            )
            _status_badge(True, f"Loaded {len(nexus_tools_local)} tool(s)")
        except Exception as exc:  # noqa: BLE001
            _status_badge(False, "Failed to load tools", detail=str(exc))
        if show_names:
            with st.expander("Tool names", expanded=False):
                st.code("\n".join(_tool_names(nexus_tools_local)) or "<none>", language="text")

    st.markdown("---")
    return k8s_tools_local, docker_tools_local, nexus_tools_local


k8s_tools, docker_tools, nexus_tools = _render_mcp_tools_fragment()


def _run_checks() -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True}

    if k8s_tools:
        try:
            out["k8s_health"] = _invoke(k8s_tools, "health_check", {})
        except Exception as exc:  # noqa: BLE001
            out["k8s_health"] = {"ok": False, "error": str(exc)}

        try:
            out["helm_health"] = _invoke(k8s_tools, "helm_health_check", {})
        except Exception as exc:  # noqa: BLE001
            out["helm_health"] = {"ok": False, "error": str(exc)}
    else:
        out["k8s_health"] = {"ok": False, "error": "Kubernetes tools not loaded"}
        out["helm_health"] = {"ok": False, "error": "Helm tools not loaded (kubernetes-mcp unavailable)"}

    if docker_tools:
        try:
            out["docker_health"] = _invoke(docker_tools, "health_check", {})
        except Exception as exc:  # noqa: BLE001
            out["docker_health"] = {"ok": False, "error": str(exc)}
    else:
        out["docker_health"] = {"ok": False, "error": "Docker tools not loaded"}

    if nexus_tools:
        try:
            out["nexus_health"] = _invoke(nexus_tools, "nexus_health_check", {})
        except Exception as exc:  # noqa: BLE001
            out["nexus_health"] = {"ok": False, "error": str(exc)}
    else:
        out["nexus_health"] = {"ok": False, "error": "Nexus tools not loaded"}

    # Cluster objects (namespace scoped)
    if k8s_tools:
        calls: List[Tuple[str, str, Dict[str, Any]]] = [
            ("namespaces", "list_namespaces", {}),
            ("deployments", "list_deployments", {"namespace": namespace}),
            ("pods", "list_pods", {"namespace": namespace}),
            ("services", "list_services", {"namespace": namespace}),
            ("events", "list_events", {"namespace": namespace, "limit": 200}),
            ("service_accounts", "list_service_accounts", {"namespace": namespace}),
        ]
        for out_key, tool_name, args in calls:
            try:
                out[out_key] = _invoke(k8s_tools, tool_name, args)
            except Exception as exc:  # noqa: BLE001
                out[out_key] = {"ok": False, "error": str(exc)}

    # Helm releases (via kubernetes-mcp)
    if k8s_tools:
        try:
            out["releases"] = _invoke(k8s_tools, "helm_list_releases", {"all_namespaces": True})
        except Exception as exc:  # noqa: BLE001
            out["releases"] = {"ok": False, "error": str(exc)}

    return out


@st.fragment
def _render_status_fragment() -> Dict[str, Any]:
    """Run expensive health/resource checks in a fragment.

    This keeps the rest of the Setup page responsive: changing inputs/widgets
    won't re-run network calls unless you explicitly refresh.
    """

    st.subheader("Connectivity")
    st.caption("Click refresh to run MCP health checks and inventory calls.")

    col_a, col_b = st.columns([1, 1])
    with col_a:
        refresh_now = st.button("Refresh status", use_container_width=True, key="setup_refresh_status")
    with col_b:
        keep_auto = st.toggle(
            "Auto-refresh when missing",
            value=False,
            key="setup_auto_refresh_status",
            help="If enabled, the first visit will run checks automatically.",
        )

    cached = _cache_get("status")
    if refresh_now or (keep_auto and not isinstance(cached, dict)):
        with st.spinner("Running checks…"):
            cached = _run_checks()
        _cache_set("status", cached)

    if not isinstance(cached, dict):
        st.info("No status yet. Click 'Refresh status'.")
        return {}

    return cached


status = _render_status_fragment()


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




releases = _as_list(status.get("releases"), "releases")
pods = _as_list(status.get("pods"), "pods")
deployments = _as_list(status.get("deployments"), "deployments")
services = _as_list(status.get("services"), "services")
events = _as_list(status.get("events"), "events")

rel_match = [r for r in releases if str(r.get("name")) == str(release_name)]
rel_ns_match = [r for r in rel_match if (not namespace) or str(r.get("namespace")) == str(namespace)]

bad_deployments = [d for d in deployments if (d.get("readyReplicas") or 0) < (d.get("replicas") or 0)]
non_running_pods = [p for p in pods if str(p.get("phase")) != "Running" or not _ready_ok(p.get("ready"))]
warning_events = [e for e in events if str(e.get("type")) == "Warning"]


DOCKERFILE_BY_COMPONENT: Dict[str, str] = {
    "streamlit": "deploy/streamlit/Dockerfile",
    "mcpJenkins": "deploy/mcp-jenkins/Dockerfile",
    "mcpKubernetes": "deploy/mcp-kubernetes/Dockerfile",
    "agentDataGen": "deploy/agents/datagen/Dockerfile",
    "agentJenkins": "deploy/agents/jenkins/Dockerfile",
    "agentKubernetes": "deploy/agents/kubernetes/Dockerfile",
}


def _required_images(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    expected = _expected_components(values)
    images_cfg = values.get("images") if isinstance(values.get("images"), dict) else {}

    out: List[Dict[str, Any]] = []
    for comp_key, enabled in expected.items():
        img = images_cfg.get(comp_key) if isinstance(images_cfg.get(comp_key), dict) else {}
        repo = str(img.get("repository") or "").strip()
        tag = str(img.get("tag") or "").strip()
        local_ref = f"{repo}:{tag}" if repo and tag else ""
        dockerfile_rel = DOCKERFILE_BY_COMPONENT.get(comp_key)

        out.append(
            {
                "component": comp_key,
                "enabled": bool(enabled),
                "repository": repo,
                "tag": tag,
                "local_ref": local_ref,
                "dockerfile": dockerfile_rel,
            }
        )
    return out


def _docker_local_tags() -> set[str]:
    if not docker_tools:
        return set()
    res = _invoke(docker_tools, "list_images", {})
    if not isinstance(res, dict) or not res.get("ok"):
        _cache_set("docker_last_error", res)
        return set()
    tags: set[str] = set()
    for img in (res.get("images") if isinstance(res, dict) else []) or []:
        for t in (img.get("tags") or []) if isinstance(img, dict) else []:
            if isinstance(t, str) and t:
                tags.add(t)
    return tags


def _nexus_component_exists(*, repository_name: str, image_name: str, tag: str) -> Dict[str, Any]:
    if not nexus_tools:
        return {"ok": False, "error": "nexus tools not loaded"}
    if not repository_name:
        return {"ok": False, "error": "nexus repository name is required"}

    res = _invoke(
        nexus_tools,
        "nexus_search_components",
        {
            "repository": repository_name,
            "format": "docker",
            "name": image_name,
            "version": tag,
        },
    )
    items = (res.get("items") if isinstance(res, dict) else []) or []
    return {"ok": bool(res.get("ok", True)), "count": len(items), "items": items, "raw": res}


def render_images_pipeline() -> None:
    st.subheader("Step 1 — Images (build → push → verify)")
    st.caption(
        "Both deployment modes should start here: build images via docker-mcp, push them to a Nexus Docker hosted registry, "
        "then verify availability via nexus-mcp."
    )

    required = _required_images(values)
    required = [r for r in required if r.get("enabled")]

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        registry_host = st.text_input(
            "Docker registry host",
            value=str(st.session_state.get("setup_registry_host", "")),
            placeholder="nexus.yourdomain:8083",
            key="setup_registry_host",
            help="Used for tagging/pushing images, e.g. nexus:8083",
        ).strip().rstrip("/")
    with col_b:
        nexus_repo = st.text_input(
            "Nexus repo name (docker hosted)",
            value=str(st.session_state.get("setup_nexus_repo", "docker-hosted")),
            key="setup_nexus_repo",
            help="The Nexus Repository name (not the image name).",
        ).strip()
    with col_c:
        do_login = st.checkbox(
            "Docker login before push",
            value=bool(st.session_state.get("setup_docker_login", False)),
            key="setup_docker_login",
        )

    login_user = ""
    login_pass = ""
    if do_login:
        lu, lp = st.columns(2)
        with lu:
            login_user = st.text_input(
                "Registry username",
                value=str(cfg.nexus.username or ""),
                key="setup_registry_user",
            )
        with lp:
            login_pass = st.text_input(
                "Registry password",
                value=str(cfg.nexus.password or ""),
                type="password",
                key="setup_registry_pass",
            )

    local_tags = _cache_get("local_image_tags")
    if not isinstance(local_tags, set):
        local_tags = set()

    if st.button("Refresh image status", use_container_width=True, key="setup_refresh_images"):
        try:
            _cache_set("local_image_tags", _docker_local_tags())
        except Exception:
            _cache_set("local_image_tags", set())

    local_tags = _cache_get("local_image_tags")
    if not isinstance(local_tags, set):
        local_tags = set()

    rows: List[Dict[str, Any]] = []
    for r in required:
        repo = str(r.get("repository") or "")
        tag = str(r.get("tag") or "")
        local_ref = str(r.get("local_ref") or "")
        remote_ref = f"{registry_host}/{local_ref}" if registry_host and local_ref else ""
        rows.append(
            {
                "component": r.get("component"),
                "local": local_ref,
                "local_present": local_ref in local_tags,
                "remote": remote_ref,
                "dockerfile": r.get("dockerfile"),
            }
        )

    st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("Docker MCP debug", expanded=False):
        if not docker_tools:
            st.error("docker-mcp tools not loaded")
        else:
            try:
                health = _invoke(docker_tools, "health_check", {})
            except Exception as exc:  # noqa: BLE001
                health = {"ok": False, "error": str(exc)}
            st.markdown("##### health_check")
            _render_jsonish(health)

            try:
                imgs = _invoke(docker_tools, "list_images", {})
            except Exception as exc:  # noqa: BLE001
                imgs = {"ok": False, "error": str(exc)}
            st.markdown("##### list_images")
            if isinstance(imgs, dict) and imgs.get("ok"):
                st.caption(f"images: {len(imgs.get('images') or [])}")
                _render_jsonish((imgs.get("images") or [])[:5])
            else:
                _render_jsonish(imgs)

            try:
                cont = _invoke(docker_tools, "list_containers", {"all": True})
            except Exception as exc:  # noqa: BLE001
                cont = {"ok": False, "error": str(exc)}
            st.markdown("##### list_containers (all)")
            if isinstance(cont, dict) and cont.get("ok"):
                st.caption(f"containers: {len(cont.get('containers') or [])}")
                _render_jsonish((cont.get("containers") or [])[:10])
            else:
                _render_jsonish(cont)

            last_err = _cache_get("docker_last_error")
            if last_err:
                st.warning("docker-mcp last error (from list_images)")
                _render_jsonish(last_err)

    b1, b2, b3 = st.columns(3)
    with b1:
        build_all = st.button("Build all (local)", use_container_width=True)
    with b2:
        push_all = st.button("Tag + push all (to registry)", use_container_width=True)
    with b3:
        check_all = st.button("Check images in Nexus", use_container_width=True)

    if build_all:
        if not docker_tools:
            st.error("docker-mcp tools not loaded")
        else:
            for r in required:
                df = r.get("dockerfile")
                if not df:
                    st.warning(f"No Dockerfile mapped for {r.get('component')}")
                    continue
                st.write(f"Building {r.get('local_ref')}...")
                res = _invoke(
                    docker_tools,
                    "build_image",
                    {
                        "context_path": str(ROOT),
                        "dockerfile": str(df),
                        "tag": str(r.get("local_ref")),
                    },
                )
                st.json(res)
            try:
                _cache_set("local_image_tags", _docker_local_tags())
            except Exception:
                pass

    if push_all:
        if not docker_tools:
            st.error("docker-mcp tools not loaded")
        elif not registry_host:
            st.error("Set 'Docker registry host' first")
        else:
            if do_login:
                st.write("Logging in...")
                st.json(_invoke(docker_tools, "docker_login", {"registry": registry_host, "username": login_user, "password": login_pass}))
            for r in required:
                local_ref = str(r.get("local_ref"))
                remote_ref = f"{registry_host}/{local_ref}"
                st.write(f"Tagging {local_ref} -> {remote_ref}")
                st.json(_invoke(docker_tools, "tag_image", {"source": local_ref, "target": remote_ref}))
                st.write(f"Pushing {remote_ref}")
                st.json(_invoke(docker_tools, "push_image", {"ref": remote_ref}))

    if check_all:
        if not nexus_tools:
            st.error("nexus-mcp tools not loaded")
        else:
            results: Dict[str, Any] = {}
            for r in required:
                repo = str(r.get("repository") or "")
                tag = str(r.get("tag") or "")
                if not repo or not tag:
                    continue
                results[str(r.get("component"))] = _nexus_component_exists(
                    repository_name=nexus_repo,
                    image_name=repo,
                    tag=tag,
                )
            _cache_set("nexus_image_checks", results)

    nexus_checks = _cache_get("nexus_image_checks")
    if isinstance(nexus_checks, dict) and nexus_checks:
        summary_rows: List[Dict[str, Any]] = []
        for comp, r in nexus_checks.items():
            if not isinstance(r, dict):
                continue
            count = int(r.get("count") or 0)
            items = r.get("items") if isinstance(r.get("items"), list) else []
            first = items[0] if items else {}
            summary_rows.append(
                {
                    "component": comp,
                    "found": bool(count > 0),
                    "count": count,
                    "name": first.get("name") if isinstance(first, dict) else None,
                    "version": first.get("version") if isinstance(first, dict) else None,
                    "id": first.get("id") if isinstance(first, dict) else None,
                }
            )

        st.markdown("##### Nexus verification")
        st.dataframe(summary_rows, use_container_width=True, hide_index=True)
        with st.expander("Nexus image check results (raw)", expanded=False):
            st.json(nexus_checks)


render_images_pipeline()


tabs = st.tabs(["Using Kubernetes", "Using Helm"])

with tabs[0]:
    st.subheader("Deploy using Kubernetes manifests")
    st.caption(
        "Applies manifests under deploy/k8s via kubectl (through kubernetes-mcp). "
        "Note: these manifests currently hardcode `best-streamlit-website` in metadata.namespace."
    )

    manifest_dir = ROOT / "deploy" / "k8s"
    manifest_names = [
        "mcp-jenkins.yaml",
        "mcp-kubernetes.yaml",
        "mcp-sonarqube.yaml",
        "agent-datagen.yaml",
        "agent-jenkins.yaml",
        "agent-kubernetes.yaml",
        "streamlit.yaml",
    ]
    manifest_paths = [manifest_dir / n for n in manifest_names if (manifest_dir / n).is_file()]
    default_selected = [str(p) for p in manifest_paths]

    selected = st.multiselect(
        "Select manifests",
        options=[str(p) for p in manifest_paths],
        default=default_selected,
        key="setup_selected_manifests",
    )

    a1, a2 = st.columns(2)
    with a1:
        do_apply = st.button("Apply selected", type="primary", use_container_width=True)
    with a2:
        do_delete = st.button("Delete selected", use_container_width=True)

    if do_apply:
        if not k8s_tools:
            st.error("kubernetes-mcp tools not loaded")
        else:
            for p in selected:
                st.write(f"Applying: {p}")
                st.json(_invoke(k8s_tools, "kubectl_apply", {"file_path": p}))
            _cache_set("status", _run_checks())
            st.rerun()

    if do_delete:
        if not k8s_tools:
            st.error("kubernetes-mcp tools not loaded")
        else:
            for p in selected:
                st.write(f"Deleting: {p}")
                st.json(_invoke(k8s_tools, "kubectl_delete", {"file_path": p}))
            _cache_set("status", _run_checks())
            st.rerun()

    with st.expander("Kubernetes resources (namespace scoped)", expanded=True):
        st.markdown("#### Deployments")
        if deployments:
            st.dataframe(deployments, use_container_width=True, hide_index=True)
        if bad_deployments:
            st.error("Some deployments are not ready")
            st.dataframe(bad_deployments, use_container_width=True, hide_index=True)

        st.markdown("#### Pods")
        if pods:
            st.dataframe(pods, use_container_width=True, hide_index=True)
        if non_running_pods:
            st.warning("Some pods are not healthy (phase/ready)")
            st.dataframe(non_running_pods, use_container_width=True, hide_index=True)

        st.markdown("#### Services")
        if services:
            st.dataframe(services, use_container_width=True, hide_index=True)

        st.markdown("#### Events")
        if warning_events:
            st.error("Warning events detected")
            st.dataframe(warning_events[:80], use_container_width=True, hide_index=True)
        elif events:
            st.dataframe(events[:80], use_container_width=True, hide_index=True)
        else:
            st.json(status.get("events"))

    with st.expander("Pod logs", expanded=False):
        if k8s_tools and pods:
            pod_options = [f"{p.get('namespace')}/{p.get('name')}" for p in pods if p.get("name") and p.get("namespace")]
            picked_pod = st.selectbox("Pod", options=[""] + pod_options, index=0, key="setup_pod_logs_picker")
            tail = st.number_input("Tail lines", min_value=10, max_value=5000, value=200, step=50, key="setup_pod_logs_tail")
            if picked_pod and st.button("Fetch logs", use_container_width=True, key="setup_fetch_logs"):
                ns, name = picked_pod.split("/", 1)
                st.json(_invoke(k8s_tools, "get_pod_logs", {"name": name, "namespace": ns, "tail_lines": int(tail)}))
        else:
            st.info("Load Kubernetes tools and ensure pods exist to view logs.")


with tabs[1]:
    st.subheader("Deploy / upgrade using Helm")
    st.caption("Uses Helm tools exposed by kubernetes-mcp. Helm may be unreliable on some Windows setups; use the Kubernetes tab as the fallback.")

    if not k8s_tools:
        st.warning("Kubernetes MCP tools not available. Fix kubernetes-mcp connectivity first.")
    else:
        d1, d2, d3, d4 = st.columns([2, 1, 1, 1])
        with d1:
            chart_path = st.text_input(
                "Chart path",
                value=str(CHART_DIR.resolve()),
                help="Local path to deploy/helm/best-streamlit-website.",
            )
        with d2:
            deploy_wait = st.checkbox("Wait", value=True)
        with d3:
            deploy_atomic = st.checkbox("Atomic", value=False)
        with d4:
            deploy_dry_run = st.checkbox("Dry run", value=False)

        timeout = st.text_input("Timeout", value="10m")
        values_mode = st.radio("Values source", options=["Use values.yaml", "Inline YAML"], horizontal=True)
        if values_mode == "Inline YAML":
            values_yaml = st.text_area("values.yaml (inline)", value=values_text, height=200)
        else:
            values_yaml = values_text
            st.caption(f"Using {VALUES_PATH}")

        if st.button("Helm upgrade --install", type="primary", use_container_width=True, key="setup_helm_deploy"):
            try:
                res = _invoke(
                    k8s_tools,
                    "helm_upgrade_install_release",
                    {
                        "release": release_name,
                        "chart": chart_path,
                        "namespace": namespace,
                        "create_namespace": True,
                        "values_yaml": values_yaml,
                        "wait": bool(deploy_wait),
                        "atomic": bool(deploy_atomic),
                        "timeout": str(timeout or "10m"),
                        "dry_run": bool(deploy_dry_run),
                    },
                )
                st.success("Helm operation completed.")
                st.json(res)
                _cache_set("status", _run_checks())
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Helm deploy failed: {exc}")

    with st.expander("Helm releases", expanded=True):
        if not releases:
            _render_jsonish(status.get("releases"))
        else:
            view = rel_match if rel_match else releases
            st.dataframe(view, use_container_width=True, hide_index=True)

        if rel_ns_match and k8s_tools:
            picked = rel_ns_match[0]
            rel_ns = str(picked.get("namespace") or namespace)
            st.success(f"Found release '{release_name}' in namespace '{rel_ns}'.")
            try:
                rel_status = _invoke(k8s_tools, "helm_get_release_status", {"release": release_name, "namespace": rel_ns})
            except Exception as exc:  # noqa: BLE001
                rel_status = {"ok": False, "error": str(exc)}
            _render_jsonish(rel_status)


with st.expander("Connectivity", expanded=False):
    cols = st.columns(4)
    with cols[0]:
        st.markdown("##### Kubernetes")
        _render_jsonish(status.get("k8s_health"))
    with cols[1]:
        st.markdown("##### Helm")
        _render_jsonish(status.get("helm_health"))
    with cols[2]:
        st.markdown("##### Docker")
        _render_jsonish(status.get("docker_health"))
    with cols[3]:
        st.markdown("##### Nexus")
        _render_jsonish(status.get("nexus_health"))


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
