import asyncio
import json
import inspect
import os
from datetime import datetime
from typing import Any, Dict, List

import streamlit as st

from src.theme import set_theme
from src.ai.agents.jenkins_agent import build_jenkins_agent


set_theme(page_title="Agent Management", page_icon="🤖")

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
    username: str,
    token: str,
    verify_ssl: bool,
) -> Dict[str, str]:
    """Build environment for the Jenkins FastMCP subprocess.

    This mirrors the expectations in src/ai/mcp_servers/jenkins_server.py,
    ensuring the MCP server sees the same credentials as the direct client.
    """

    env = dict(os.environ)
    if base_url:
        env["JENKINS_BASE_URL"] = base_url
    if username:
        env["JENKINS_USERNAME"] = username
    if token:
        env["JENKINS_API_TOKEN"] = token
    env["JENKINS_VERIFY_SSL"] = "true" if verify_ssl else "false"
    return env


if "jenkins_tool_calls" not in st.session_state:
    st.session_state.jenkins_tool_calls: List[Dict[str, Any]] = []
if "jenkins_last_plan" not in st.session_state:
    st.session_state.jenkins_last_plan: Dict[str, Any] | None = None


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
    username = st.text_input("Username (optional)", value=st.session_state.get("jenkins_username", ""))
    token = st.text_input("API token or password (optional)", type="password", value=st.session_state.get("jenkins_token", ""))
    verify_ssl = st.checkbox("Verify SSL certificates", value=st.session_state.get("jenkins_verify_ssl", True))

    if st.button("Test connection", type="primary"):
        st.session_state.jenkins_base_url = base_url
        st.session_state.jenkins_username = username
        st.session_state.jenkins_token = token
        st.session_state.jenkins_verify_ssl = verify_ssl
        try:
            agent = build_jenkins_agent(
                base_url,
                username,
                token,
                verify_ssl,
                user_name=st.session_state.get("current_username", "Adham"),
            )
            result = agent.server.get_server_info()
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
            st.session_state.jenkins_messages: List[Dict[str, str]] = []

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
                                st.session_state.get("jenkins_username", username),
                                st.session_state.get("jenkins_token", token),
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
                            username,
                            token,
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

        from pathlib import Path
        from langchain_mcp_adapters.client import MultiServerMCPClient

        # Discover available tools from the Jenkins FastMCP server via
        # MultiServerMCPClient.get_tools(), following the official docs.
        # Pass through the current Jenkins credentials so the MCP
        # subprocess authenticates correctly (avoiding 403s).
        try:
            env = _build_jenkins_env(
                st.session_state.get("jenkins_base_url", base_url),
                st.session_state.get("jenkins_username", username),
                st.session_state.get("jenkins_token", token),
                st.session_state.get("jenkins_verify_ssl", verify_ssl),
            )

            server_path = (
                Path(__file__).resolve().parent.parent
                / "src"
                / "ai"
                / "mcp_servers"
                / "jenkins_server.py"
            )

            client = MultiServerMCPClient(
                {
                    "jenkins": {
                        "transport": "stdio",
                        "command": "python",
                        "args": [str(server_path)],
                        "env": env,
                    }
                }
            )

            mcp_tools = asyncio.run(client.get_tools())
        except Exception as exc:  # noqa: BLE001
            st.error(f"Error fetching tools from Jenkins MCP server: {exc}")
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

        from pathlib import Path
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.callbacks import Callbacks, CallbackContext
        from langchain_mcp_adapters.interceptors import MCPToolCallRequest

        if "mcp_progress_events" not in st.session_state:
            st.session_state.mcp_progress_events: List[Dict[str, Any]] = []
        if "mcp_log_events" not in st.session_state:
            st.session_state.mcp_log_events: List[Dict[str, Any]] = []
        if "mcp_tool_call_events" not in st.session_state:
            st.session_state.mcp_tool_call_events: List[Dict[str, Any]] = []
        if "mcp_tools_cache" not in st.session_state:
            st.session_state.mcp_tools_cache: List[Dict[str, Any]] = []

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
                    entry: Dict[str, Any] = {
                        "server": request.server_name,
                        "tool": request.name,
                        "args": request.args,
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
                        st.session_state.get("jenkins_username", username),
                        st.session_state.get("jenkins_token", token),
                        st.session_state.get("jenkins_verify_ssl", verify_ssl),
                    )

                    server_path = (
                        Path(__file__).resolve().parent.parent
                        / "src"
                        / "ai"
                        / "mcp_servers"
                        / "jenkins_server.py"
                    )

                    client = MultiServerMCPClient(
                        {
                            "jenkins": {
                                "transport": "stdio",
                                "command": "python",
                                "args": [str(server_path)],
                                "env": env,
                            }
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
    st.markdown("- Keep your Jenkins API token scoped to what the agent should be allowed to do.")

st.markdown("</div>", unsafe_allow_html=True)
