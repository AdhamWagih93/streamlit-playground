import asyncio
import json
import inspect
import os
from datetime import datetime
from typing import Any, Dict, List

import streamlit as st

from src.admin_config import load_admin_config
from src.theme import set_theme
from src.ai.agents.jenkins_agent import build_jenkins_agent


set_theme(page_title="Agent Management", page_icon="🤖")

admin = load_admin_config()
if not admin.is_agent_enabled("jenkins_agent", default=True):
    st.info("Jenkins tool agent is disabled by Admin.")
    st.stop()

# Persist a simple user identity for contextual prompts
if "current_username" not in st.session_state:
    st.session_state.current_username = "Adham"

st.markdown(
    """
    <style>
    .agent-layout { max-width: 1200px; margin: 0 auto; }
    .agent-hero {
        background: linear-gradient(120deg, #0b63d6, #6c5ce7, #00b894);
        border-radius: 18px;
        padding: 1.7rem 1.6rem 1.4rem 1.6rem;
        margin-bottom: 1.2rem;
        color: #fff;
        box-shadow: 0 12px 32px rgba(11, 99, 214, 0.35);
    }
    .agent-hero-title {
        font-size: 1.7rem;
        font-weight: 800;
        letter-spacing: 0.06em;
        margin-bottom: 0.35rem;
    }
    .agent-hero-sub { font-size: 0.98rem; opacity: 0.95; }
    .agent-card {
        background: linear-gradient(145deg, #ffffff, #f3f6fb);
        border-radius: 18px;
        padding: 1.0rem 1.1rem 0.9rem 1.1rem;
        box-shadow: 0 6px 24px rgba(15, 23, 42, 0.10);
        border: 1px solid #d3ddec;
    }
    .agent-chat-card {
        display: flex;
        flex-direction: column;
        gap: 0.6rem;
        max-height: 560px;
        min-height: 420px;
    }
    .agent-chat-history {
        flex: 1 1 auto;
        overflow-y: auto;
        padding-right: 0.5rem;
        margin-top: 0.3rem;
        margin-bottom: 0.3rem;
    }
    .agent-msg {
        margin-bottom: 0.4rem;
        padding: 0.45rem 0.6rem;
        border-radius: 12px;
        font-size: 0.9rem;
    }
    .agent-msg-user {
        background: #e0f2fe;
        margin-left: 0.5rem;
    }
    .agent-msg-agent {
        background: #eef2ff;
        margin-right: 0.5rem;
    }
    .agent-msg-label {
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        opacity: 0.7;
        margin-bottom: 0.15rem;
    }
    .agent-timeline-item {
        border-left: 3px solid #0b63d6;
        padding-left: 0.7rem;
        margin-bottom: 0.6rem;
    }
    .agent-timeline-title { font-weight: 600; }
    .agent-timeline-meta { font-size: 0.75rem; color: #64748b; }
    .agent-json {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        font-size: 0.75rem;
        background: #0f172a;
        color: #e5e7eb;
        border-radius: 10px;
        padding: 0.6rem 0.7rem;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .agent-duration-row {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        margin-top: 0.15rem;
        margin-bottom: 0.35rem;
    }
    .agent-duration-label {
        font-size: 0.72rem;
        color: #64748b;
        min-width: 64px;
    }
    .agent-duration-bar-bg {
        flex: 1 1 auto;
        height: 6px;
        border-radius: 999px;
        background: #e2e8f0;
        overflow: hidden;
    }
    .agent-duration-bar-fill {
        height: 100%;
        border-radius: inherit;
        background: linear-gradient(90deg, #0b63d6, #22c55e);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _build_jenkins_env(
    base_url: str,
    verify_ssl: bool,
) -> Dict[str, str]:
    """Build environment for the Jenkins FastMCP subprocess.

    Jenkins credentials are configured server-side via env vars
    (e.g., JENKINS_USERNAME/JENKINS_API_TOKEN). The UI only passes runtime
    connection settings like base URL + SSL verify, plus the required MCP
    client auth token.

    The MCP server process is launched via module entrypoint:
    `python -m src.ai.mcp_servers.jenkins.mcp`.
    """

    from src.ai.mcp_servers.jenkins.config import JenkinsMCPServerConfig
    from src.streamlit_config import get_app_config

    cfg = get_app_config()
    env = dict(os.environ)

    effective = JenkinsMCPServerConfig(
        base_url=base_url or cfg.jenkins.base_url,
        username=cfg.jenkins.username,
        api_token=cfg.jenkins.api_token,
        verify_ssl=verify_ssl,
        mcp_client_token=cfg.jenkins.mcp_client_token,
        mcp_transport=cfg.jenkins.mcp_transport,
        mcp_host=cfg.jenkins.mcp_host,
        mcp_port=cfg.jenkins.mcp_port,
        mcp_url=cfg.jenkins.mcp_url,
    )

    return {**env, **effective.to_env_overrides()}


if "jenkins_tool_calls" not in st.session_state:
    st.session_state.jenkins_tool_calls = []  # type: ignore[assignment]
if "jenkins_last_plan" not in st.session_state:
    st.session_state.jenkins_last_plan = None  # type: ignore[assignment]


st.markdown("<div class='agent-layout'>", unsafe_allow_html=True)

st.markdown(
    """
    <div class="agent-hero">
      <div class="agent-hero-title">Agent Management • Jenkins MCP</div>
      <div class="agent-hero-sub">
        Connect to a Jenkins instance, issue natural-language queries through
        a Jenkins agent, and inspect every underlying tool call and plan.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

conn_col, _ = st.columns([1.5, 1])
with conn_col:
    st.subheader("Jenkins connection")
    base_url = st.text_input("Jenkins base URL", value=st.session_state.get("jenkins_base_url", "http://localhost:8080"))
    verify_ssl = st.checkbox("Verify SSL certificates", value=st.session_state.get("jenkins_verify_ssl", True))

    st.caption(
        "Jenkins credentials are configured on the MCP server host via env vars "
        "`JENKINS_USERNAME` and `JENKINS_API_TOKEN` (not collected in the UI). "
        "MCP client auth uses `JENKINS_MCP_CLIENT_TOKEN`."
    )

    if st.button("Test connection", type="primary"):
        st.session_state.jenkins_base_url = base_url
        st.session_state.jenkins_verify_ssl = verify_ssl
        try:
            agent = build_jenkins_agent(
                base_url,
                None,
                None,
                verify_ssl,
                user_name=st.session_state.get("current_username", "Adham"),
            )
            result = agent.call_tool("get_server_info", {})
            if result.get("ok"):
                st.success(f"Connected to Jenkins at {result.get('url')}")
            else:
                st.error(f"Request failed: {result}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Error connecting to Jenkins: {exc}")

st.markdown("---")

main_col, side_col = st.columns([2.2, 1])

with main_col:
    tabs = st.tabs(["Jenkins Agent", "Direct Tool Call", "Plans & Flow", "MCP Servers"])

    # --- Jenkins Agent tab ---
    with tabs[0]:
        st.markdown("<div class='agent-card agent-chat-card'>", unsafe_allow_html=True)
        st.markdown("### Chat with Jenkins agent", unsafe_allow_html=False)
        st.caption("Describe what you want to inspect or trigger on Jenkins.")

        use_chat_api = hasattr(st, "chat_input") and hasattr(st, "chat_message")
        if "jenkins_messages" not in st.session_state:
            st.session_state.jenkins_messages = []  # type: ignore[assignment]

        if use_chat_api:
            for msg in st.session_state.jenkins_messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            prompt = st.chat_input("Ask the Jenkins agent…")
            if prompt:
                st.session_state.jenkins_messages.append({"role": "user", "content": prompt})
                with st.chat_message("user"):
                    st.markdown(prompt)

                # Stream the assistant's reply as it is generated
                with st.chat_message("assistant"):
                    placeholder = st.empty()
                    chunks: List[str] = []

                    def on_token(token: str) -> None:
                        chunks.append(token)
                        placeholder.markdown("".join(chunks))

                    with st.spinner("Agent planning and executing Jenkins calls…"):
                        try:
                            agent = build_jenkins_agent(
                                st.session_state.get("jenkins_base_url", base_url),
                                None,
                                None,
                                st.session_state.get("jenkins_verify_ssl", verify_ssl),
                                user_name=st.session_state.get("current_username", "Adham"),
                            )
                            result = agent.run_with_stream(
                                prompt,
                                on_token=on_token,
                                history=st.session_state.get("jenkins_messages", []),
                            )
                            st.session_state.jenkins_last_plan = result
                            st.session_state.jenkins_tool_calls = result.get("tool_calls", [])
                            answer = result.get("final_response") or ("".join(chunks) or "(no response)")
                        except Exception as exc:  # noqa: BLE001
                            answer = f"Error: {exc}"
                            placeholder.markdown(answer)

                st.session_state.jenkins_messages.append({"role": "assistant", "content": answer})
        else:
            # Fallback: simple text area and button
            if "jenkins_messages" not in st.session_state:
                st.session_state.jenkins_messages = []

            # Compact, scrollable chat history so the input stays visible
            history_html = ""
            for msg in st.session_state.jenkins_messages:
                role_label = "You" if msg["role"] == "user" else "Jenkins Agent"
                css_class = "agent-msg-user" if msg["role"] == "user" else "agent-msg-agent"
                history_html += (
                    f"<div class='agent-msg {css_class}'>"
                    f"<div class='agent-msg-label'>{role_label}</div>"
                    f"<div>{msg['content']}</div>"
                    "</div>"
                )
            st.markdown(f"<div class='agent-chat-history'>{history_html}</div>", unsafe_allow_html=True)

            prompt = st.text_input("Your Jenkins request", key="jenkins_prompt")
            if st.button("Run via Jenkins agent") and prompt.strip():
                st.session_state.jenkins_messages.append({"role": "user", "content": prompt})
                stream_placeholder = st.empty()

                def on_token(token: str) -> None:
                    current = st.session_state.get("_jenkins_stream_text", "") + token
                    st.session_state._jenkins_stream_text = current
                    stream_placeholder.markdown(current)

                with st.spinner("Agent planning and executing Jenkins calls…"):
                    try:
                        agent = build_jenkins_agent(
                            base_url,
                            None,
                            None,
                            verify_ssl,
                            user_name=st.session_state.get("current_username", "Adham"),
                        )
                        # Reset temp stream buffer
                        st.session_state._jenkins_stream_text = ""
                        result = agent.run_with_stream(
                            prompt,
                            on_token=on_token,
                            history=st.session_state.get("jenkins_messages", []),
                        )
                        st.session_state.jenkins_last_plan = result
                        st.session_state.jenkins_tool_calls = result.get("tool_calls", [])
                        answer = result.get("final_response") or st.session_state.get("_jenkins_stream_text", "(no response)")
                    except Exception as exc:  # noqa: BLE001
                        answer = f"Error: {exc}"
                        stream_placeholder.markdown(answer)

                st.session_state.jenkins_messages.append({"role": "assistant", "content": answer})
                st.success("Response received from Jenkins agent.")

        if st.button("Clear Jenkins conversation", type="secondary"):
            st.session_state.jenkins_messages = []
            st.session_state.jenkins_tool_calls = []
            st.session_state.jenkins_last_plan = None
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    # --- Direct Tool Call tab ---
    with tabs[1]:
        st.markdown("<div class='agent-card'>", unsafe_allow_html=True)
        st.markdown("### Call Jenkins tools directly", unsafe_allow_html=False)
        st.caption(
            "Select a Jenkins function and provide JSON arguments to call it "
            "directly against the MCP server.",
        )

        from langchain_mcp_adapters.client import MultiServerMCPClient

        # Discover available tools from the Jenkins FastMCP server via
        # MultiServerMCPClient.get_tools(), following the official docs.
        # IMPORTANT: this can be slow, so it's opt-in.

        def _direct_tools_sig() -> str:
            from src.streamlit_config import get_app_config

            env = _build_jenkins_env(
                st.session_state.get("jenkins_base_url", base_url),
                st.session_state.get("jenkins_verify_ssl", verify_ssl),
            )
            app_cfg = get_app_config()
            j_transport = (app_cfg.jenkins.mcp_transport or "stdio").lower().strip()
            j_transport = "sse" if j_transport == "http" else j_transport
            return json.dumps(
                {
                    "transport": j_transport,
                    "url": app_cfg.jenkins.mcp_url,
                    # Don't include secrets; just capture presence.
                    "env_keys": sorted([k for k in env.keys() if k.startswith("JENKINS_")]),
                },
                sort_keys=True,
            )

        def _load_direct_tools(*, force: bool = False) -> List[Any]:
            from src.streamlit_config import get_app_config

            sig = _direct_tools_sig()
            cache_key = "_jenkins_direct_tools"
            sig_key = "_jenkins_direct_tools_sig"
            if (not force) and st.session_state.get(sig_key) == sig and cache_key in st.session_state:
                return list(st.session_state.get(cache_key) or [])

            env = _build_jenkins_env(
                st.session_state.get("jenkins_base_url", base_url),
                st.session_state.get("jenkins_verify_ssl", verify_ssl),
            )

            app_cfg = get_app_config()
            j_transport = (app_cfg.jenkins.mcp_transport or "stdio").lower().strip()
            j_transport = "sse" if j_transport == "http" else j_transport

            if j_transport == "stdio":
                conn = {
                    "transport": "stdio",
                    "command": "python",
                    "args": ["-m", "src.ai.mcp_servers.jenkins.mcp"],
                    "env": env,
                }
            else:
                conn = {
                    "transport": "sse",
                    "url": app_cfg.jenkins.mcp_url,
                }

            client = MultiServerMCPClient({"jenkins": conn})
            tools_local = asyncio.run(client.get_tools())
            st.session_state[cache_key] = tools_local
            st.session_state[sig_key] = sig
            return list(tools_local or [])

        b1, b2 = st.columns([1, 3])
        with b1:
            load_now = st.button("Load/refresh tools", use_container_width=True)
        with b2:
            st.caption("Tool discovery can take a few seconds (runs on demand).")

        mcp_tools = st.session_state.get("_jenkins_direct_tools")
        if load_now:
            try:
                with st.spinner("Discovering Jenkins tools via MCP…"):
                    mcp_tools = _load_direct_tools(force=True)
                st.success("Tools loaded.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Error fetching tools from Jenkins MCP server: {exc}")
                st.markdown("</div>", unsafe_allow_html=True)
                st.stop()

        if not mcp_tools:
            st.info("Tools are not loaded yet. Click **Load/refresh tools**.")
            st.markdown("</div>", unsafe_allow_html=True)
            st.stop()

        if not mcp_tools:
            st.error("No Jenkins tools were discovered via MCP.")
            st.markdown("</div>", unsafe_allow_html=True)
            st.stop()

        tool_choices = sorted([t.name for t in mcp_tools])
        tool_name = st.selectbox("Tool", options=tool_choices)

        spec = next(t for t in mcp_tools if t.name == tool_name)
        selected_tool = spec

        # Use MCP description directly for the tool summary.
        first_line = (getattr(spec, "description", "") or "No description available.").splitlines()[0]

        st.markdown(f"**Description:** {first_line}")

        # Build a simple parameter template from the MCP args schema.
        # Derive a simple JSON schema from the tool's args_schema, if present.
        schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        args_schema = getattr(spec, "args_schema", None)
        if args_schema is not None and hasattr(args_schema, "schema"):
            try:
                schema = args_schema.schema()  # type: ignore[assignment]
            except Exception:  # noqa: BLE001
                pass
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", [])) if isinstance(schema, dict) else set()

        template: Dict[str, Any] = {}
        param_help_lines: List[str] = []
        for name, meta in props.items():
            if name == "_client_token":
                continue
            placeholder: Any
            ptype = str(meta.get("type", "string"))
            if ptype == "integer":
                placeholder = 0
            elif ptype == "boolean":
                placeholder = True
            else:
                placeholder = "<value>" if name in required else "<optional>"

            template[name] = placeholder
            human_placeholder = json.dumps(placeholder)
            param_help_lines.append(f"- `{name}` → {human_placeholder}")

        if template:
            st.markdown("**Parameters & example values:**")
            st.markdown("\n".join(param_help_lines))

        template_json = json.dumps(template, indent=2) if template else "{}"
        st.caption("Start from this JSON template and replace only the placeholder values:")
        st.code(template_json, language="json")

        # Ensure the textarea shows the template when switching between tools
        args_key = f"tool_args_{tool_name}"
        last_tool = st.session_state.get("_direct_tool_last")
        if last_tool != tool_name or args_key not in st.session_state:
            st.session_state[args_key] = template_json
        st.session_state["_direct_tool_last"] = tool_name

        args_text = st.text_area(
            "Arguments (JSON)",
            value=st.session_state.get(args_key, template_json),
            height=160,
            key=args_key,
        )

        if st.button("Execute tool"):
            try:
                args = json.loads(args_text) if args_text.strip() else {}
                if not isinstance(args, dict):
                    raise ValueError("Arguments JSON must decode to an object.")

                # Inject required MCP client auth token automatically.
                from src.streamlit_config import get_app_config

                cfg = get_app_config()
                args["_client_token"] = cfg.jenkins.mcp_client_token

                with st.spinner(f"Calling {tool_name} via MCP…"):
                    # MCP tools are async-first; prefer ainvoke via asyncio.
                    if hasattr(selected_tool, "ainvoke"):
                        result = asyncio.run(selected_tool.ainvoke(args))
                    else:
                        result = selected_tool.invoke(args)
                st.success("Tool call completed.")
                st.code(json.dumps(result, indent=2, default=str), language="json")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Error executing tool: {exc}")

        st.markdown("</div>", unsafe_allow_html=True)

    # --- Plans & Flow tab ---
    with tabs[2]:
        st.markdown("<div class='agent-card'>", unsafe_allow_html=True)
        st.markdown("### Jenkins agent plans & tool flow", unsafe_allow_html=False)
        st.caption(
            "Inspect the last agent plan JSON and each concrete Jenkins tool "
            "call that was executed.",
        )

        plan = st.session_state.get("jenkins_last_plan")
        calls = st.session_state.get("jenkins_tool_calls", [])

        if not plan and not calls:
            st.info("Run a request via the Jenkins agent tab to see its plan and flow here.")
        else:
            # LLM summary / reasoning
            if isinstance(plan, dict):
                summary_text = plan.get("final_response")
                if summary_text:
                    st.markdown("#### LLM summary / reasoning")
                    st.markdown(summary_text)

            with st.expander("Raw plan JSON from the LLM", expanded=False):
                raw_plan = plan.get("raw_plan") if isinstance(plan, dict) else None
                if raw_plan:
                    st.markdown(f"<div class='agent-json'>{raw_plan}</div>", unsafe_allow_html=True)
                else:
                    st.code(json.dumps(plan, indent=2), language="json")

            st.markdown("#### Executed tool calls")
            if not calls:
                st.caption("No tool calls were recorded for the last request.")
            else:
                # Pre-compute durations to normalise visual bars
                durations: List[int | None] = []
                for c in calls:
                    started = c.get("started_at")
                    finished = c.get("finished_at")
                    duration_ms: int | None
                    try:
                        if isinstance(started, str):
                            s_dt = datetime.fromisoformat(started.replace("Z", ""))
                            f_dt = datetime.fromisoformat(finished.replace("Z", "")) if isinstance(finished, str) else s_dt
                            duration_ms = int((f_dt - s_dt).total_seconds() * 1000)
                        else:
                            duration_ms = None
                    except Exception:  # noqa: BLE001
                        duration_ms = None
                    durations.append(duration_ms)

                non_null = [d for d in durations if d is not None]
                max_duration = max(non_null) if non_null else None

                for idx, (c, duration_ms) in enumerate(zip(calls, durations), start=1):
                    st.markdown("<div class='agent-timeline-item'>", unsafe_allow_html=True)
                    st.markdown(
                        f"<div class='agent-timeline-title'>Step {idx}: {c.get('name')}</div>",
                        unsafe_allow_html=True,
                    )
                    meta = f"Status: {'OK' if c.get('ok') else 'ERROR'}"
                    if duration_ms is not None:
                        meta += f" • Duration: {duration_ms} ms"
                    st.markdown(
                        f"<div class='agent-timeline-meta'>{meta}</div>",
                        unsafe_allow_html=True,
                    )
                    if duration_ms is not None and max_duration and max_duration > 0:
                        # Ensure very small calls are still visible with a minimum width
                        pct = max(6, int(duration_ms / max_duration * 100))
                        st.markdown(
                            "<div class='agent-duration-row'>"
                            f"<span class='agent-duration-label'>{duration_ms} ms</span>"
                            "<div class='agent-duration-bar-bg'>"
                            f"<div class='agent-duration-bar-fill' style='width:{pct}%;'></div>"
                            "</div></div>",
                            unsafe_allow_html=True,
                        )
                    st.markdown("**Arguments:**")
                    st.code(json.dumps(c.get("args", {}), indent=2), language="json")
                    st.markdown("**Tool response (preview):**")
                    st.markdown(f"<div class='agent-json'>{c.get('result_preview','')}</div>", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

    # --- MCP Servers tab ---
    with tabs[3]:
        st.markdown("<div class='agent-card'>", unsafe_allow_html=True)
        st.markdown("### MCP Servers • Jenkins FastMCP", unsafe_allow_html=False)
        st.caption(
            "Inspect the Jenkins FastMCP server via MultiServerMCPClient: "
            "tools, resources, and live callbacks (progress, logs, and tool calls).",
        )

        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.callbacks import Callbacks, CallbackContext
        from langchain_mcp_adapters.interceptors import MCPToolCallRequest

        if "mcp_progress_events" not in st.session_state:
            st.session_state.mcp_progress_events = []  # type: ignore[assignment]
        if "mcp_log_events" not in st.session_state:
            st.session_state.mcp_log_events = []  # type: ignore[assignment]
        if "mcp_tool_call_events" not in st.session_state:
            st.session_state.mcp_tool_call_events = []  # type: ignore[assignment]
        if "mcp_tools_cache" not in st.session_state:
            st.session_state.mcp_tools_cache = []  # type: ignore[assignment]

        col_a, col_b = st.columns([1.2, 1])

        with col_a:
            st.markdown("#### Inspect Jenkins MCP server")

            if st.button("Refresh tools & resources from MCP server", type="primary"):
                st.session_state.mcp_progress_events = []
                st.session_state.mcp_log_events = []
                st.session_state.mcp_tool_call_events = []
                progress_events: List[Dict[str, Any]] = st.session_state.mcp_progress_events
                log_events: List[Dict[str, Any]] = st.session_state.mcp_log_events

                async def on_progress(
                    progress: float,
                    total: float | None,
                    message: str | None,
                    context: CallbackContext,
                ) -> None:
                    event = {
                        "server": context.server_name,
                        "tool": context.tool_name,
                        "progress": progress,
                        "total": total,
                        "message": message,
                        "time": datetime.utcnow().isoformat() + "Z",
                    }
                    progress_events.append(event)

                async def on_logging_message(params, context: CallbackContext) -> None:  # type: ignore[override]
                    event = {
                        "server": context.server_name,
                        "level": getattr(params, "level", None),
                        "data": getattr(params, "data", None),
                        "time": datetime.utcnow().isoformat() + "Z",
                    }
                    log_events.append(event)

                callbacks = Callbacks(
                    on_progress=on_progress,
                    on_logging_message=on_logging_message,
                )

                # Simple logging interceptor for MCP tool calls
                async def logging_interceptor(
                    request: MCPToolCallRequest,
                    handler,
                ):
                    started = datetime.utcnow()
                    safe_args: Dict[str, Any]
                    try:
                        safe_args = dict(request.args or {})
                        if "_client_token" in safe_args:
                            safe_args["_client_token"] = "***redacted***"
                    except Exception:  # noqa: BLE001
                        safe_args = {}
                    entry: Dict[str, Any] = {
                        "server": request.server_name,
                        "tool": request.name,
                        "args": safe_args,
                        "started_at": started.isoformat() + "Z",
                    }
                    try:
                        result = await handler(request)
                        entry["ok"] = True
                        entry["result_preview"] = str(getattr(result, "content", result))[:800]
                    except Exception as exc:  # noqa: BLE001
                        entry["ok"] = False
                        entry["result_preview"] = f"ERROR: {exc}"[:800]
                    finally:
                        entry["finished_at"] = datetime.utcnow().isoformat() + "Z"
                        st.session_state.mcp_tool_call_events.append(entry)
                    return result

                try:
                    # Use the Jenkins MCP client helper with callbacks and interceptor.
                    env = _build_jenkins_env(
                        st.session_state.get("jenkins_base_url", base_url),
                        st.session_state.get("jenkins_verify_ssl", verify_ssl),
                    )

                    from src.streamlit_config import get_app_config

                    app_cfg = get_app_config()
                    j_transport = (app_cfg.jenkins.mcp_transport or "stdio").lower().strip()
                    j_transport = "sse" if j_transport == "http" else j_transport

                    if j_transport == "stdio":
                        conn = {
                            "transport": "stdio",
                            "command": "python",
                            "args": ["-m", "src.ai.mcp_servers.jenkins.mcp"],
                            "env": env,
                        }
                    else:
                        conn = {
                            "transport": "sse",
                            "url": app_cfg.jenkins.mcp_url,
                        }

                    client = MultiServerMCPClient(
                        {
                            "jenkins": conn
                        },
                        callbacks=callbacks,
                        tool_interceptors=[logging_interceptor],
                    )

                    tools = asyncio.run(client.get_tools())

                    # Cache a serialisable view of tools for display
                    # Cache a serialisable view of tools for display
                    cache: List[Dict[str, Any]] = []
                    for t in tools:
                        schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
                        args_schema = getattr(t, "args_schema", None)
                        if args_schema is not None and hasattr(args_schema, "schema"):
                            try:
                                schema = args_schema.schema()  # type: ignore[assignment]
                            except Exception:  # noqa: BLE001
                                pass
                        cache.append(
                            {
                                "name": getattr(t, "name", ""),
                                "description": getattr(t, "description", ""),
                                "schema": schema,
                            }
                        )

                    st.session_state.mcp_tools_cache = cache

                    st.success("Refreshed Jenkins MCP tools via MultiServerMCPClient.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Error inspecting MCP server: {exc}")

            # Quick stats + tool list for better situational awareness
            st.markdown("#### Tools discovered via MCP")
            tools_cache = st.session_state.mcp_tools_cache
            if not tools_cache:
                st.caption("Click the refresh button above to load tools from the Jenkins MCP server.")
            else:
                total_tools = len(tools_cache)
                total_calls = len(st.session_state.mcp_tool_call_events)
                total_logs = len(st.session_state.mcp_log_events)

                m1, m2, m3 = st.columns(3)
                m1.metric("Tools", total_tools)
                m2.metric("MCP tool calls", total_calls)
                m3.metric("Log entries", total_logs)

                for tool_info in tools_cache:
                    st.markdown(f"**{tool_info['name']}**")
                    st.caption(tool_info.get("description") or "(no description)")
                    with st.expander("View input schema", expanded=False):
                        st.json(tool_info.get("schema", {}))

        with col_b:
            st.markdown("#### MCP callbacks & tool calls")

            with st.expander("Progress notifications", expanded=False):
                if not st.session_state.mcp_progress_events:
                    st.caption("No progress notifications received yet.")
                else:
                    for ev in st.session_state.mcp_progress_events:
                        pct = (
                            (ev["progress"] / ev["total"] * 100)
                            if ev.get("total")
                            else ev.get("progress")
                        )
                        st.markdown(
                            f"- `{ev['time']}` • **{ev.get('server') or 'jenkins'} / {ev.get('tool') or '-'}** "
                            f"→ {pct:.1f}% — {ev.get('message') or ''}"
                        )

            with st.expander("Server log messages", expanded=False):
                if not st.session_state.mcp_log_events:
                    st.caption("No log messages received yet.")
                else:
                    for ev in st.session_state.mcp_log_events:
                        st.markdown(
                            f"- `{ev['time']}` • **{ev.get('server') or 'jenkins'}** "
                            f"[{ev.get('level')}] — {ev.get('data')}"
                        )

            with st.expander("Tool call interceptor timeline", expanded=False):
                if not st.session_state.mcp_tool_call_events:
                    st.caption("No MCP tool calls captured yet.")
                else:
                    # Highlight basic stats at the top
                    ok_calls = [e for e in st.session_state.mcp_tool_call_events if e.get("ok")]
                    err_calls = [e for e in st.session_state.mcp_tool_call_events if not e.get("ok")]
                    st.markdown(
                        f"Total calls: **{len(st.session_state.mcp_tool_call_events)}** · "
                        f"✅ {len(ok_calls)} OK · ❌ {len(err_calls)} errors"
                    )

                    # Then render the detailed timeline
                    for ev in st.session_state.mcp_tool_call_events:
                        status = "OK" if ev.get("ok") else "ERROR"
                        st.markdown(
                            f"**{ev.get('tool')}** on **{ev.get('server') or 'jenkins'}** — {status}"
                        )
                        st.caption(
                            f"Started: {ev.get('started_at')} • Finished: {ev.get('finished_at')}"
                        )
                        st.markdown("**Args:**")
                        st.code(json.dumps(ev.get("args", {}), indent=2, default=str), language="json")
                        st.markdown("**Result (preview):**")
                        st.code(str(ev.get("result_preview", "")), language="text")

        st.markdown("</div>", unsafe_allow_html=True)

with side_col:
    st.markdown("<div class='agent-card'>", unsafe_allow_html=True)
    st.markdown("**What this page does**")
    st.markdown(
        "- Connects to Jenkins and sends natural-language queries to a Jenkins agent.\n"
        "- Lets you call individual Jenkins MCP tools directly.\n"
        "- Shows the LLM plan JSON and every concrete Jenkins tool call.",
    )
    st.markdown("---")
    st.markdown("**Tips**")
    st.markdown("- Start with read-only queries (jobs, builds, nodes) before triggering new builds.")
    st.markdown("- Keep the Jenkins API token (server-side) scoped to what this agent should be allowed to do.")

st.markdown("</div>", unsafe_allow_html=True)
