from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import streamlit as st

from src.admin_config import AdminConfig, DEFAULT_CONFIG_PATH, load_admin_config, save_admin_config
from src.page_catalog import catalog_by_group, known_page_paths
from src.streamlit_config import get_app_config


def _coerce_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _coerce_bool(x: Any, default: bool) -> bool:
    if x is None:
        return bool(default)
    return bool(x)


def _get_server_cfg(admin: AdminConfig, server_name: str) -> Dict[str, Any]:
    raw = (admin.mcp_servers or {}).get(server_name, {})
    return raw if isinstance(raw, dict) else {}


def _get_agent_cfg(admin: AdminConfig, agent_name: str) -> Dict[str, Any]:
    raw = (admin.agents or {}).get(agent_name, {})
    return raw if isinstance(raw, dict) else {}


def _query_params_get(key: str) -> Optional[str]:
    try:
        qp = st.query_params  # type: ignore[attr-defined]
        val = qp.get(key)
        if val is None:
            return None
        if isinstance(val, list):
            return str(val[0]) if val else None
        return str(val)
    except Exception:
        try:
            qp = st.experimental_get_query_params()  # type: ignore[attr-defined]
            v = qp.get(key)
            return str(v[0]) if isinstance(v, list) and v else None
        except Exception:
            return None


def _query_params_set(**kwargs: Any) -> None:
    """Best-effort set/clear query params across Streamlit versions.

    IMPORTANT: Do not blindly clear *all* query params.
    Streamlit's navigation/routing may store state in the URL.
    """

    # Read existing params first.
    existing: Dict[str, List[str]] = {}
    try:
        qp = st.query_params  # type: ignore[attr-defined]
        try:
            # st.query_params behaves like a mapping; values may be str or list[str].
            for k in qp.keys():
                v = qp.get(k)
                if v is None:
                    continue
                if isinstance(v, list):
                    existing[str(k)] = [str(x) for x in v]
                else:
                    existing[str(k)] = [str(v)]
        except Exception:
            existing = {}
    except Exception:
        try:
            raw = st.experimental_get_query_params()  # type: ignore[attr-defined]
            for k, v in (raw or {}).items():
                if isinstance(v, list):
                    existing[str(k)] = [str(x) for x in v]
                else:
                    existing[str(k)] = [str(v)]
        except Exception:
            existing = {}

    # Apply changes.
    updated = dict(existing)
    for k, v in kwargs.items():
        key = str(k)
        if v is None:
            updated.pop(key, None)
        else:
            updated[key] = [str(v)]

    # Write back.
    try:
        qp = st.query_params  # type: ignore[attr-defined]
        # Clear only keys we manage (those present in kwargs) to preserve navigation state.
        for k, v in kwargs.items():
            key = str(k)
            try:
                if v is None and key in qp:
                    del qp[key]
            except Exception:
                pass
            if v is not None:
                qp[key] = str(v)
        return
    except Exception:
        pass

    try:
        st.experimental_set_query_params(**updated)  # type: ignore[attr-defined]
    except Exception:
        return


def _open_settings() -> None:
    _query_params_set(settings="1")


def _close_settings() -> None:
    _query_params_set(settings=None)


def inject_settings_launcher() -> None:
    """Render a fixed-position settings cog that opens Settings via query params."""

    css = """
    <style>
    .bsw-settings-btn {
        position: fixed;
        top: 0.8rem;
        right: 1.0rem;
        z-index: 100000;
        width: 40px;
        height: 40px;
        border-radius: 999px;
        display: grid;
        place-items: center;
        text-decoration: none;
        background: rgba(255,255,255,0.75);
        border: 1px solid rgba(148,163,184,0.55);
        box-shadow: 0 8px 22px -10px rgba(2,6,23,0.35);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
        color: #0f172a;
        font-size: 18px;
        font-weight: 700;
    }
    .bsw-settings-btn:hover {
        transform: translateY(-1px);
        box-shadow: 0 14px 30px -14px rgba(2,6,23,0.45);
    }
    .bsw-settings-btn:active {
        transform: translateY(0px) scale(0.98);
    }
    @media (max-width: 640px) {
        .bsw-settings-btn { top: 0.65rem; right: 0.65rem; }
    }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)

    # Streamlit-native fallback button (reliable even if HTML/CSS is affected).
    # This renders at the top of the main content and is right-aligned.
    cols = st.columns([0.92, 0.08])
    with cols[1]:
        if st.button("⚙️", key="bsw_open_settings_btn", help="Settings"):
            _open_settings()
            st.rerun()

    # Use a simple anchor to set query params without JS.
    st.markdown(
        "<a class='bsw-settings-btn' href='?settings=1' title='Settings' aria-label='Settings'>⚙️</a>",
        unsafe_allow_html=True,
    )


def _clear_runtime_caches() -> int:
    """Clear known MCP/tool caches stored in st.session_state.

    This helps ensure that disabling a server takes effect immediately.
    """

    prefixes = (
        "_docker_",
        "_nexus_",
        "_k8s_",
        "mcp_",
    )
    extra_keys = {
        "k8s_snapshot",
        "k8s_terminal_history",
        "k8s_terminal_selected",
        "k8s_terminal_last",
    }

    to_delete: List[str] = []
    for k in list(st.session_state.keys()):
        if k in extra_keys:
            to_delete.append(k)
            continue
        if any(str(k).startswith(p) for p in prefixes):
            to_delete.append(k)
            continue

    for k in to_delete:
        st.session_state.pop(k, None)

    return len(to_delete)


def _render_overview(admin: AdminConfig) -> None:
    st.markdown("### Overview")
    st.caption("Settings are persisted locally (non-secret). Secrets stay in env vars.")

    col_a, col_b, col_c = st.columns([1.4, 1.0, 1.0])

    with col_a:
        st.write("**Config file**")
        st.code(str(DEFAULT_CONFIG_PATH))
        st.write("**Updated at**")
        st.write(str(getattr(admin, "updated_at", "")))

    with col_b:
        total_pages = sum(1 for _g, specs in catalog_by_group().items() for s in specs if not s.always_visible)
        enabled_pages = sum(
            1
            for _g, specs in catalog_by_group().items()
            for s in specs
            if (s.always_visible or admin.is_page_enabled(s.path, default=True))
        )
        st.metric("Pages visible", f"{enabled_pages}/{enabled_pages + (total_pages - (enabled_pages - 1 if enabled_pages else 0))}" if total_pages else str(enabled_pages))

    with col_c:
        enabled_servers = sum(1 for s in ["jenkins", "kubernetes", "docker", "nexus"] if admin.is_mcp_enabled(s, default=True))
        st.metric("MCP servers enabled", str(enabled_servers))

    st.divider()

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Clear cached MCP/tools", type="secondary", use_container_width=True):
            n = _clear_runtime_caches()
            st.success(f"Cleared {n} cached session keys.")

    with col2:
        if st.button("Close settings", type="secondary", use_container_width=True):
            _close_settings()
            st.rerun()


def _render_pages_section(admin: AdminConfig) -> bool:
    st.markdown("### Navigation")
    st.caption("Choose which pages appear in the top navigation. Home is always visible.")

    changed = False

    for group, specs in catalog_by_group().items():
        with st.expander(group, expanded=(group in {"Home"})):
            for spec in specs:
                if spec.always_visible:
                    st.toggle(
                        f"{spec.title} (always visible)",
                        value=True,
                        disabled=True,
                        key=f"settings_page_toggle_{spec.path}",
                    )
                    continue

                current = admin.is_page_enabled(spec.path, default=True)
                new_val = st.toggle(
                    spec.title,
                    value=bool(current),
                    key=f"settings_page_toggle_{spec.path}",
                )
                if bool(new_val) != bool(current):
                    admin.pages[spec.path] = bool(new_val)
                    changed = True

    return changed


def _render_mcp_servers_section(admin: AdminConfig) -> bool:
    st.markdown("### MCP servers")
    st.caption(
        "Enable/disable MCP servers and override non-secret fields used by the UI. "
        "Credentials/tokens remain env-driven."
    )

    cfg = get_app_config()
    changed = False

    servers: List[str] = ["jenkins", "kubernetes", "docker", "nexus"]
    tabs = st.tabs([s.capitalize() for s in servers])

    for tab, server in zip(tabs, servers):
        with tab:
            cur = _get_server_cfg(admin, server)
            enabled = _coerce_bool(cur.get("enabled", True), True)

            new_enabled = st.toggle(
                "Enabled",
                value=enabled,
                key=f"settings_mcp_enabled_{server}",
            )
            if bool(new_enabled) != bool(enabled):
                admin.mcp_servers.setdefault(server, {})
                admin.mcp_servers[server]["enabled"] = bool(new_enabled)
                changed = True

            st.divider()

            if server == "jenkins":
                st.markdown("**Overrides (non-secret)**")
                base_url = st.text_input(
                    "Base URL",
                    value=str(cur.get("base_url") or ""),
                    placeholder=str(getattr(cfg.jenkins, "base_url", "")),
                    key="settings_jenkins_base_url",
                )
                verify_ssl = st.toggle(
                    "Verify SSL",
                    value=_coerce_bool(cur.get("verify_ssl", getattr(cfg.jenkins, "verify_ssl", True)), True),
                    key="settings_jenkins_verify_ssl",
                )
                transport = st.selectbox(
                    "Transport",
                    options=["http"],
                    index=0,
                    key="settings_jenkins_transport",
                )
                url = st.text_input(
                    "Remote MCP URL (http transport)",
                    value=str(cur.get("url") or ""),
                    placeholder=str(getattr(cfg.jenkins, "mcp_url", "")),
                    key="settings_jenkins_url",
                )

                def _set(k: str, v: Any) -> None:
                    admin.mcp_servers.setdefault(server, {})
                    admin.mcp_servers[server][k] = v

                if base_url.strip() != str(cur.get("base_url") or "").strip():
                    _set("base_url", base_url.strip() or None)
                    changed = True
                if bool(verify_ssl) != _coerce_bool(cur.get("verify_ssl", getattr(cfg.jenkins, "verify_ssl", True)), True):
                    _set("verify_ssl", bool(verify_ssl))
                    changed = True
                if str(transport).strip() != str(cur.get("transport") or "").strip():
                    _set("transport", str(transport).strip())
                    changed = True
                if url.strip() != str(cur.get("url") or "").strip():
                    _set("url", url.strip() or None)
                    changed = True

            elif server == "kubernetes":
                st.markdown("**Overrides (non-secret)**")
                transport = st.selectbox(
                    "Transport",
                    options=["http"],
                    index=0,
                    key="settings_kubernetes_transport",
                )
                url = st.text_input(
                    "Remote MCP URL (http transport)",
                    value=str(cur.get("url") or ""),
                    placeholder=str(getattr(cfg.kubernetes, "mcp_url", "")),
                    key="settings_kubernetes_url",
                )

                admin.mcp_servers.setdefault(server, {})
                if str(transport).strip() != str(cur.get("transport") or "").strip():
                    admin.mcp_servers[server]["transport"] = str(transport).strip()
                    changed = True
                if url.strip() != str(cur.get("url") or "").strip():
                    admin.mcp_servers[server]["url"] = url.strip() or None
                    changed = True

            elif server == "docker":
                st.markdown("**Overrides (non-secret)**")
                transport = st.selectbox(
                    "Transport",
                    options=["http"],
                    index=0,
                    key="settings_docker_transport",
                )
                url = st.text_input(
                    "Remote MCP URL (http transport)",
                    value=str(cur.get("url") or ""),
                    placeholder=str(getattr(cfg.docker, "mcp_url", "")),
                    key="settings_docker_url",
                )

                admin.mcp_servers.setdefault(server, {})
                if str(transport).strip() != str(cur.get("transport") or "").strip():
                    admin.mcp_servers[server]["transport"] = str(transport).strip()
                    changed = True
                if url.strip() != str(cur.get("url") or "").strip():
                    admin.mcp_servers[server]["url"] = url.strip() or None
                    changed = True

            elif server == "nexus":
                st.markdown("**Overrides (non-secret)**")
                base_url = st.text_input(
                    "Base URL",
                    value=str(cur.get("base_url") or ""),
                    placeholder=str(getattr(cfg.nexus, "base_url", "")),
                    key="settings_nexus_base_url",
                )
                verify_ssl = st.toggle(
                    "Verify SSL",
                    value=_coerce_bool(cur.get("verify_ssl", getattr(cfg.nexus, "verify_ssl", True)), True),
                    key="settings_nexus_verify_ssl",
                )
                allow_raw = st.toggle(
                    "Allow raw commands",
                    value=_coerce_bool(cur.get("allow_raw", getattr(cfg.nexus, "allow_raw", False)), False),
                    key="settings_nexus_allow_raw",
                )
                transport = st.selectbox(
                    "Transport",
                    options=["http"],
                    index=0,
                    key="settings_nexus_transport",
                )
                url = st.text_input(
                    "Remote MCP URL (http transport)",
                    value=str(cur.get("url") or ""),
                    placeholder=str(getattr(cfg.nexus, "mcp_url", "")),
                    key="settings_nexus_url",
                )

                admin.mcp_servers.setdefault(server, {})
                if base_url.strip() != str(cur.get("base_url") or "").strip():
                    admin.mcp_servers[server]["base_url"] = base_url.strip() or None
                    changed = True
                if bool(verify_ssl) != _coerce_bool(cur.get("verify_ssl", getattr(cfg.nexus, "verify_ssl", True)), True):
                    admin.mcp_servers[server]["verify_ssl"] = bool(verify_ssl)
                    changed = True
                if bool(allow_raw) != _coerce_bool(cur.get("allow_raw", getattr(cfg.nexus, "allow_raw", False)), False):
                    admin.mcp_servers[server]["allow_raw"] = bool(allow_raw)
                    changed = True
                if str(transport).strip() != str(cur.get("transport") or "").strip():
                    admin.mcp_servers[server]["transport"] = str(transport).strip()
                    changed = True
                if url.strip() != str(cur.get("url") or "").strip():
                    admin.mcp_servers[server]["url"] = url.strip() or None
                    changed = True

            st.caption("Tip: clear an override field to fall back to environment config.")

    return changed


def _render_agents_section(admin: AdminConfig) -> bool:
    st.markdown("### Agents")
    st.caption("Enable/disable agents and configure non-secret runtime settings.")

    changed = False

    tabs = st.tabs(["DataGen", "Jenkins tool agent", "Kubernetes tool agent"])

    with tabs[0]:
        cur = _get_agent_cfg(admin, "datagen")
        enabled = _coerce_bool(cur.get("enabled", True), True)
        new_enabled = st.toggle(
            "Enabled",
            value=enabled,
            key="settings_agent_datagen_enabled",
        )
        if bool(new_enabled) != bool(enabled):
            admin.agents.setdefault("datagen", {})
            admin.agents["datagen"]["enabled"] = bool(new_enabled)
            changed = True

        st.divider()
        st.markdown("**Ollama / LLM overrides (non-secret)**")

        model = st.text_input(
            "Model",
            value=str(cur.get("model") or ""),
            placeholder="llama3.2:3b",
            key="settings_agent_datagen_model",
        )
        base_url = st.text_input(
            "Ollama base URL",
            value=str(cur.get("ollama_base_url") or ""),
            placeholder="http://localhost:11434",
            key="settings_agent_datagen_base_url",
        )
        temperature = st.slider(
            "Temperature",
            min_value=0.0,
            max_value=2.0,
            value=_coerce_float(cur.get("temperature"), 0.2),
            step=0.05,
            key="settings_agent_datagen_temperature",
        )

        admin.agents.setdefault("datagen", {})

        if model.strip() != str(cur.get("model") or "").strip():
            admin.agents["datagen"]["model"] = model.strip() or None
            changed = True
        if base_url.strip() != str(cur.get("ollama_base_url") or "").strip():
            admin.agents["datagen"]["ollama_base_url"] = base_url.strip() or None
            changed = True
        if float(temperature) != _coerce_float(cur.get("temperature"), float(temperature)):
            admin.agents["datagen"]["temperature"] = float(temperature)
            changed = True

        st.caption("DataGen agent reads overrides dynamically; changes apply on next run.")

    with tabs[1]:
        cur = _get_agent_cfg(admin, "jenkins_agent")
        enabled = _coerce_bool(cur.get("enabled", True), True)
        new_enabled = st.toggle(
            "Enabled",
            value=enabled,
            key="settings_agent_jenkins_enabled",
        )
        if bool(new_enabled) != bool(enabled):
            admin.agents.setdefault("jenkins_agent", {})
            admin.agents["jenkins_agent"]["enabled"] = bool(new_enabled)
            changed = True
        st.caption("Currently enable/disable only.")

    with tabs[2]:
        cur = _get_agent_cfg(admin, "kubernetes_agent")
        enabled = _coerce_bool(cur.get("enabled", True), True)
        new_enabled = st.toggle(
            "Enabled",
            value=enabled,
            key="settings_agent_kubernetes_enabled",
        )
        if bool(new_enabled) != bool(enabled):
            admin.agents.setdefault("kubernetes_agent", {})
            admin.agents["kubernetes_agent"]["enabled"] = bool(new_enabled)
            changed = True
        st.caption("Currently enable/disable only.")

    return changed


def _render_raw_json(admin: AdminConfig) -> bool:
    st.markdown("### Advanced (Raw JSON)")
    st.caption("Power-user view. This is the exact JSON persisted to disk.")

    changed = False

    try:
        raw = admin.to_json()
    except Exception:
        raw = json.dumps(admin.to_dict(), indent=2)

    edited = st.text_area(
        "admin_config.json",
        value=raw,
        height=360,
        key="settings_raw_json",
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Validate JSON", use_container_width=True):
            try:
                _ = AdminConfig.from_json(edited, known_pages=known_page_paths())
                st.success("Valid JSON.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Invalid JSON: {exc}")

    with col2:
        if st.button("Apply JSON", type="primary", use_container_width=True):
            try:
                new_cfg = AdminConfig.from_json(edited, known_pages=known_page_paths())
                save_admin_config(new_cfg)
                st.success("Saved.")
                # Ensure we don't keep stale caches
                _clear_runtime_caches()
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not apply JSON: {exc}")

    return changed


def maybe_render_settings_dialog() -> None:
    """If query param settings=1 is present, render the Settings dialog."""

    if _query_params_get("settings") not in {"1", "true", "yes", "on"}:
        return

    admin = load_admin_config(known_pages=known_page_paths())

    @st.dialog("Settings", width="large")
    def _dlg() -> None:
        # Refresh in case another session saved changes.
        admin_local = load_admin_config(known_pages=known_page_paths())

        tabs = st.tabs(["Overview", "Navigation", "MCP", "Agents", "Advanced"])

        with tabs[0]:
            _render_overview(admin_local)

        any_changed = False
        with tabs[1]:
            any_changed = _render_pages_section(admin_local) or any_changed

        with tabs[2]:
            any_changed = _render_mcp_servers_section(admin_local) or any_changed

        with tabs[3]:
            any_changed = _render_agents_section(admin_local) or any_changed

        with tabs[4]:
            _render_raw_json(admin_local)

        st.divider()
        col_a, col_b, col_c = st.columns([1.2, 1.2, 1.0])

        with col_a:
            if st.button("Save changes", type="primary", use_container_width=True):
                save_admin_config(admin_local)
                _clear_runtime_caches()
                st.success("Saved. Changes apply immediately.")
                st.rerun()

        with col_b:
            if st.button("Save + close", type="secondary", use_container_width=True):
                save_admin_config(admin_local)
                _clear_runtime_caches()
                _close_settings()
                st.rerun()

        with col_c:
            if st.button("Close", use_container_width=True):
                _close_settings()
                st.rerun()

        if any_changed:
            st.caption("Unsaved changes detected.")

    _dlg()


def render_global_settings() -> None:
    """Render the cog launcher and settings dialog if opened."""

    inject_settings_launcher()
    maybe_render_settings_dialog()
