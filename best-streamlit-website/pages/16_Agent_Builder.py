"""Dynamic Agent Builder - Create and chat with custom multi-MCP agents."""

import uuid

import streamlit as st
from typing import Any, Dict, List

from src.admin_config import load_admin_config
from src.theme import set_theme
from src.mcp_health import add_mcp_status_styles


set_theme(page_title="Agent Builder", page_icon="🤖")

admin = load_admin_config()
if not admin.is_agent_enabled("dynamic", default=True):
    st.info("Dynamic Agent Builder is disabled by Admin.")
    st.stop()

# Add status badge styles
add_mcp_status_styles()

# Custom styling
st.markdown(
    """
    <style>
    .agent-hero {
        background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #a855f7 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(99, 102, 241, 0.3);
    }
    .agent-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        letter-spacing: 0.5px;
    }
    .agent-hero p {
        margin: 0;
        font-size: 1.05rem;
        opacity: 0.95;
    }
    .agent-card {
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
        margin-bottom: 1rem;
    }
    .agent-card h3 {
        font-size: 1.2rem;
        font-weight: 700;
        margin: 0 0 1rem 0;
        color: #1e293b;
    }
    .server-chip {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.85rem;
        font-weight: 500;
        margin: 0.25rem;
    }
    .server-chip-active {
        background: #ddd6fe;
        color: #5b21b6;
    }
    .server-chip-inactive {
        background: #f1f5f9;
        color: #64748b;
    }
    .tool-call-card {
        background: #f8fafc;
        border-radius: 8px;
        padding: 0.75rem;
        margin: 0.5rem 0;
        border-left: 3px solid #6366f1;
        font-size: 0.85rem;
    }
    .tool-call-success {
        border-left-color: #22c55e;
    }
    .tool-call-error {
        border-left-color: #ef4444;
    }
    .chat-container {
        max-height: 500px;
        overflow-y: auto;
        padding: 1rem;
        background: #fafafa;
        border-radius: 12px;
        margin-bottom: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="agent-hero">
        <h1>Dynamic Agent Builder</h1>
        <p>Create custom AI agents with access to any combination of MCP servers</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Initialize session state
if "agent_runtime" not in st.session_state:
    st.session_state.agent_runtime = None
if "agent_messages" not in st.session_state:
    st.session_state.agent_messages = []
if "agent_tool_calls" not in st.session_state:
    st.session_state.agent_tool_calls = []
if "agent_config" not in st.session_state:
    st.session_state.agent_config = {
        "servers": [],
        "system_prompt": "",
        "model": "llama3.2",
    }
if "agent_session_id" not in st.session_state:
    st.session_state.agent_session_id = str(uuid.uuid4())[:8]


def _lazy_import_agent_module():
    """Lazily import the agent module to avoid startup overhead."""
    from src.ai.agents.dynamic_agent import (
        get_available_servers,
        build_dynamic_agent,
        run_agent_query,
        list_agent_tools,
        ToolCallEvent,
    )
    return get_available_servers, build_dynamic_agent, run_agent_query, list_agent_tools, ToolCallEvent


# Sidebar for agent configuration
with st.sidebar:
    st.markdown("### Agent Configuration")

    # Get available servers
    try:
        get_available_servers, build_dynamic_agent, run_agent_query, list_agent_tools, ToolCallEvent = _lazy_import_agent_module()
        servers = get_available_servers()
        import_error = None
    except ImportError as e:
        servers = {}
        import_error = str(e)

    if import_error:
        st.error(f"Failed to load agent module: {import_error}")
        st.stop()

    st.markdown("#### Select MCP Servers")
    st.caption("Choose which tools the agent can use")

    selected_servers = []
    for key, info in servers.items():
        col_check, col_info = st.columns([1, 4])
        with col_check:
            checked = st.checkbox(
                info["icon"],
                value=key in st.session_state.agent_config.get("servers", []),
                key=f"server_{key}",
                label_visibility="collapsed",
            )
        with col_info:
            st.markdown(f"**{info['icon']} {info['name']}**")
            st.caption(info["description"][:60] + "..." if len(info["description"]) > 60 else info["description"])

        if checked:
            selected_servers.append(key)

    st.session_state.agent_config["servers"] = selected_servers

    st.divider()

    st.markdown("#### System Prompt")
    system_prompt = st.text_area(
        "System prompt",
        value=st.session_state.agent_config.get("system_prompt", ""),
        height=150,
        placeholder="You are a helpful DevOps assistant...",
        help="Define the agent's personality and behavior",
        label_visibility="collapsed",
    )
    st.session_state.agent_config["system_prompt"] = system_prompt

    st.divider()

    st.markdown("#### Model Settings")

    model_options = ["llama3.2", "llama3.1", "mistral", "codellama", "tinyllama"]
    model_name = st.selectbox(
        "Model",
        options=model_options,
        index=model_options.index(st.session_state.agent_config.get("model", "llama3.2")) if st.session_state.agent_config.get("model", "llama3.2") in model_options else 0,
    )
    st.session_state.agent_config["model"] = model_name

    ollama_url = st.text_input(
        "Ollama URL",
        value=st.session_state.get("_ollama_url", "http://localhost:11434"),
        help="URL of your Ollama server",
    )
    st.session_state["_ollama_url"] = ollama_url

    temperature = st.slider(
        "Temperature",
        min_value=0.0,
        max_value=1.0,
        value=st.session_state.get("_agent_temp", 0.1),
        step=0.1,
        help="Higher = more creative, Lower = more focused",
    )
    st.session_state["_agent_temp"] = temperature

    st.divider()

    # Build agent button
    if st.button("Build Agent", use_container_width=True, type="primary"):
        if not selected_servers:
            st.error("Please select at least one MCP server")
        else:
            with st.spinner("Building agent..."):
                try:
                    # Clear previous state
                    st.session_state.agent_tool_calls = []

                    runtime = build_dynamic_agent(
                        selected_servers=selected_servers,
                        system_prompt=system_prompt,
                        model_name=model_name,
                        ollama_base_url=ollama_url,
                        temperature=temperature,
                        tool_call_events=st.session_state.agent_tool_calls,
                        session_id=st.session_state.agent_session_id,
                        source="agent_builder",
                    )
                    st.session_state.agent_runtime = runtime
                    st.success(f"Agent built with {len(runtime.tools)} tools!")
                except Exception as exc:
                    st.error(f"Failed to build agent: {exc}")
                    st.session_state.agent_runtime = None

    if st.button("Clear Chat", use_container_width=True, type="secondary"):
        st.session_state.agent_messages = []
        st.session_state.agent_tool_calls = []
        st.session_state.agent_session_id = str(uuid.uuid4())[:8]  # New session
        st.rerun()

    # Show active servers
    if st.session_state.agent_runtime:
        st.divider()
        st.markdown("#### Active Agent")
        runtime = st.session_state.agent_runtime
        st.markdown(f"**Model:** {runtime.model_name}")
        st.markdown(f"**Tools:** {len(runtime.tools)}")
        st.markdown("**Servers:**")
        for s in runtime.selected_servers:
            info = servers.get(s, {})
            st.markdown(f"- {info.get('icon', '')} {info.get('name', s)}")


# Main content
col_chat, col_tools = st.columns([2, 1])

with col_chat:
    st.markdown('<div class="agent-card">', unsafe_allow_html=True)
    st.markdown("### Chat with Agent")

    runtime = st.session_state.agent_runtime

    if not runtime:
        st.info(
            "Configure your agent in the sidebar and click **Build Agent** to start chatting.\n\n"
            "1. Select one or more MCP servers to give the agent access to those tools\n"
            "2. Optionally customize the system prompt\n"
            "3. Choose your preferred Ollama model\n"
            "4. Click Build Agent"
        )
    else:
        # Chat interface
        use_chat_api = hasattr(st, "chat_input") and hasattr(st, "chat_message")

        if use_chat_api:
            # Render existing conversation
            for msg in st.session_state.agent_messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

                    # Show tool calls for this message
                    if msg["role"] == "assistant" and msg.get("tool_calls"):
                        with st.expander(f"Tool calls ({len(msg['tool_calls'])})", expanded=False):
                            for tc in msg["tool_calls"]:
                                status_class = "tool-call-success" if tc.ok else "tool-call-error"
                                st.markdown(
                                    f"""
                                    <div class="tool-call-card {status_class}">
                                        <strong>{tc.server}</strong> / {tc.tool}<br>
                                        <code style="font-size: 0.75rem;">{str(tc.args)[:100]}...</code>
                                    </div>
                                    """,
                                    unsafe_allow_html=True,
                                )

            # Chat input
            prompt = st.chat_input("Ask your agent...")

            if prompt:
                # Add user message
                st.session_state.agent_messages.append({"role": "user", "content": prompt})
                with st.chat_message("user"):
                    st.markdown(prompt)

                # Get response
                with st.chat_message("assistant"):
                    with st.spinner("Thinking..."):
                        try:
                            # Build history (exclude last user message which is the current query)
                            history = st.session_state.agent_messages[:-1]

                            response, tool_calls = run_agent_query(
                                runtime,
                                prompt,
                                chat_history=history,
                            )

                            st.markdown(response)

                            # Show tool calls if any
                            if tool_calls:
                                with st.expander(f"Tool calls ({len(tool_calls)})", expanded=False):
                                    for tc in tool_calls:
                                        status_class = "tool-call-success" if tc.ok else "tool-call-error"
                                        st.markdown(
                                            f"""
                                            <div class="tool-call-card {status_class}">
                                                <strong>{tc.server}</strong> / {tc.tool}<br>
                                                <code style="font-size: 0.75rem;">{str(tc.args)[:100]}...</code>
                                            </div>
                                            """,
                                            unsafe_allow_html=True,
                                        )

                            # Add assistant message
                            st.session_state.agent_messages.append({
                                "role": "assistant",
                                "content": response,
                                "tool_calls": tool_calls,
                            })

                        except Exception as exc:
                            error_msg = f"Error: {exc}"
                            st.error(error_msg)
                            st.session_state.agent_messages.append({
                                "role": "assistant",
                                "content": error_msg,
                                "tool_calls": [],
                            })
        else:
            # Fallback for older Streamlit
            for msg in st.session_state.agent_messages:
                role_label = "You" if msg["role"] == "user" else "Agent"
                st.markdown(f"**{role_label}:** {msg['content']}")

            prompt = st.text_area("Your message", key="agent_prompt", height=100)

            if st.button("Send") and prompt.strip():
                st.session_state.agent_messages.append({"role": "user", "content": prompt})
                with st.spinner("Thinking..."):
                    try:
                        history = st.session_state.agent_messages[:-1]
                        response, tool_calls = run_agent_query(runtime, prompt, chat_history=history)
                        st.session_state.agent_messages.append({
                            "role": "assistant",
                            "content": response,
                            "tool_calls": tool_calls,
                        })
                    except Exception as exc:
                        st.error(f"Error: {exc}")
                st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

with col_tools:
    st.markdown('<div class="agent-card">', unsafe_allow_html=True)
    st.markdown("### Available Tools")

    runtime = st.session_state.agent_runtime

    if runtime:
        try:
            tools_info = list_agent_tools(runtime)

            # Group by server
            tools_by_server: Dict[str, List[Dict[str, Any]]] = {}
            for tool in tools_info:
                server = tool.get("server", "unknown")
                if server not in tools_by_server:
                    tools_by_server[server] = []
                tools_by_server[server].append(tool)

            st.markdown(f"**Total tools:** {len(tools_info)}")

            for server, tools in tools_by_server.items():
                server_info = servers.get(server, {"icon": "🔧", "name": server})
                with st.expander(f"{server_info.get('icon', '')} {server_info.get('name', server)} ({len(tools)})", expanded=False):
                    for tool in tools:
                        st.markdown(f"**`{tool['name']}`**")
                        if tool.get("description"):
                            st.caption(tool["description"])
        except Exception as exc:
            st.error(f"Failed to list tools: {exc}")
    else:
        st.info("Build an agent to see available tools")

    st.markdown('</div>', unsafe_allow_html=True)

    # Tool call history
    st.markdown('<div class="agent-card">', unsafe_allow_html=True)
    st.markdown("### Tool Call History")

    if st.session_state.agent_tool_calls:
        for i, tc in enumerate(reversed(st.session_state.agent_tool_calls[-10:])):
            status_icon = "✅" if tc.ok else "❌"
            st.markdown(f"{status_icon} **{tc.server}** / `{tc.tool}`")
            st.caption(f"{tc.started_at[:19]}")
    else:
        st.caption("No tool calls yet")

    st.markdown('</div>', unsafe_allow_html=True)

# Example prompts section
st.markdown('<div class="agent-card">', unsafe_allow_html=True)
st.markdown("### Example Prompts")

example_cols = st.columns(3)

examples = [
    ("Kubernetes + Docker", ["kubernetes", "docker"], [
        "List all pods in the default namespace",
        "Show me all running Docker containers",
        "Check if there are any pods in CrashLoopBackOff",
    ]),
    ("Git + Trivy", ["git", "trivy"], [
        "Show me the recent commits in the current repo",
        "Scan the current directory for vulnerabilities",
        "What's the current branch and any uncommitted changes?",
    ]),
    ("Jenkins + Nexus", ["jenkins", "nexus"], [
        "List all Jenkins jobs",
        "Show recent builds for the main pipeline",
        "List available repositories in Nexus",
    ]),
]

for col, (title, servers_needed, prompts) in zip(example_cols, examples):
    with col:
        st.markdown(f"**{title}**")
        for prompt in prompts:
            st.caption(f"• {prompt}")

st.markdown('</div>', unsafe_allow_html=True)

# Footer
st.divider()
st.caption(
    "**Tip:** The agent can use multiple tools in sequence to complete complex tasks. "
    "For best results, be specific about what you want to accomplish. "
    "Make sure Ollama is running and the selected model is available."
)
