from typing import Any, Dict, List

import streamlit as st

from src.admin_config import load_admin_config
from src.mcp_client import get_mcp_client, get_server_url
from src.mcp_health import add_mcp_status_styles
from src.streamlit_config import get_app_config
from src.theme import set_theme


set_theme(page_title="Git Explorer", page_icon="📂")

admin = load_admin_config()
if not admin.is_mcp_enabled("git", default=True):
    st.info("Git MCP is disabled by Admin.")
    st.stop()

# Add status badge styles
add_mcp_status_styles()

# Modern styling
st.markdown(
    """
    <style>
    .git-hero {
        background: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(249, 115, 22, 0.3);
    }
    .git-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        letter-spacing: 0.5px;
    }
    .git-hero p {
        margin: 0;
        font-size: 1.05rem;
        opacity: 0.95;
    }
    .git-card {
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
        margin-bottom: 1rem;
    }
    .git-card h3 {
        font-size: 1.2rem;
        font-weight: 700;
        margin: 0 0 1rem 0;
        color: #1e293b;
    }
    .commit-hash {
        font-family: monospace;
        background: #f1f5f9;
        padding: 0.2rem 0.5rem;
        border-radius: 4px;
        font-size: 0.85rem;
        color: #ea580c;
    }
    .branch-current {
        color: #059669;
        font-weight: 600;
    }
    .branch-remote {
        color: #6366f1;
    }
    .file-modified {
        color: #f59e0b;
    }
    .file-added {
        color: #059669;
    }
    .file-deleted {
        color: #dc2626;
    }
    .file-untracked {
        color: #6366f1;
    }
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 1rem;
        margin: 1rem 0;
    }
    .metric-box {
        background: #f1f5f9;
        border-radius: 12px;
        padding: 1rem;
        text-align: center;
        border: 1px solid #e2e8f0;
    }
    .metric-box-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #0f172a;
    }
    .metric-box-label {
        font-size: 0.85rem;
        color: #64748b;
        margin-top: 0.25rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


st.markdown(
    """
    <div class="git-hero">
        <h1>Git Repository Explorer</h1>
        <p>Explore and manage Git repositories via MCP server</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def _get_git_client(force_new: bool = False):
    """Get the Git MCP client."""
    return get_mcp_client("git", force_new=force_new)


def _get_git_tools(force_reload: bool = False) -> List[Dict[str, Any]]:
    """Get Git MCP tools using the unified client."""
    client = _get_git_client(force_new=force_reload)
    tools = client.list_tools(force_refresh=force_reload)
    st.session_state["_git_tools"] = tools
    st.session_state["_git_tools_sig"] = get_server_url("git")
    return tools


def _invoke(tools, name: str, args: Dict[str, Any]) -> Any:
    """Invoke a Git MCP tool."""
    client = _get_git_client()
    return client.invoke(name, args)


# Connection status info
st.subheader("Connection Status")

cfg = get_app_config()
git_url = get_server_url("git")

# Invalidate cached tools if the target URL changes
if st.session_state.get("_git_tools_sig") != git_url:
    st.session_state.pop("_git_tools", None)
    st.session_state["_git_tools_sig"] = git_url

st.markdown(
    f"""
    <div class="git-card" style="padding: 1rem;">
        <div style="color: #64748b;">Transport: <strong>streamable-http</strong> &nbsp;|&nbsp; URL: <code>{git_url}</code></div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.divider()

# Sidebar controls
with st.sidebar:
    st.markdown("### Controls")

    if "git_auto_load_tools" not in st.session_state:
        st.session_state.git_auto_load_tools = False

    st.session_state.git_auto_load_tools = st.toggle(
        "Auto-load tools on open",
        value=bool(st.session_state.git_auto_load_tools),
        help="When enabled, the page will discover tools automatically on open.",
    )

    load_clicked = st.button("Load/refresh tools", use_container_width=True)

    st.divider()

    st.markdown("### Repository Path")
    repo_path = st.text_input(
        "Repository path",
        value=st.session_state.get("_git_repo_path", cfg.git.repo_path or ""),
        placeholder="e.g., /path/to/repo or C:\\path\\to\\repo",
        help="Path to git repository. Leave empty to use current directory.",
    )
    if repo_path:
        st.session_state["_git_repo_path"] = repo_path

    st.divider()

    st.markdown("### Quick Stats")
    current_branch = st.session_state.get("_git_current_branch", "N/A")
    commits_count = len(st.session_state.get("_git_commits_list", []))
    branches_count = len(st.session_state.get("_git_branches_list", []))

    st.metric("Current Branch", current_branch)
    st.metric("Recent Commits", commits_count)
    st.metric("Branches", branches_count)

# Tool loading
should_load = bool(load_clicked) or (
    bool(st.session_state.get("git_auto_load_tools")) and "_git_tools" not in st.session_state
)

if should_load:
    try:
        with st.spinner("Loading Git MCP tools..."):
            _get_git_tools(force_reload=bool(load_clicked))
            st.success("Tools loaded successfully")
    except Exception as exc:
        st.error(f"Failed to load Git MCP tools: {exc}")
        st.info(
            "**Troubleshooting:**\n"
            "- Ensure git is installed and in your PATH\n"
            "- For remote connections, verify GIT_MCP_URL\n"
            "- Check `git --version` in your terminal"
        )

tools = st.session_state.get("_git_tools")
if not tools:
    st.info("Git tools are not loaded yet. Click **Load/refresh tools** in the sidebar to begin.")
    st.stop()

# Get repo path from session state
repo_path = st.session_state.get("_git_repo_path", cfg.git.repo_path) or None

# ==============================================================================
# QUICK ACTIONS PANEL
# ==============================================================================
st.markdown("### Quick Actions")

qa_cols = st.columns(5)

with qa_cols[0]:
    if st.button("Get Status", use_container_width=True, type="primary"):
        with st.spinner("Loading..."):
            result = _invoke(tools, "git_status", {"path": repo_path})
            if isinstance(result, dict) and result.get("ok"):
                st.session_state["_git_status"] = result
                st.success("Status loaded")
            else:
                st.error(f"Failed: {result}")

with qa_cols[1]:
    if st.button("List Commits", use_container_width=True):
        with st.spinner("Loading..."):
            result = _invoke(tools, "git_log", {"path": repo_path, "limit": 30})
            if isinstance(result, dict) and result.get("ok"):
                st.session_state["_git_commits_list"] = result.get("commits") or []
                st.success(f"Found {len(result.get('commits', []))} commits")
            else:
                st.error(f"Failed: {result}")

with qa_cols[2]:
    if st.button("List Branches", use_container_width=True):
        with st.spinner("Loading..."):
            result = _invoke(tools, "git_branches", {"path": repo_path, "all_branches": True})
            if isinstance(result, dict) and result.get("ok"):
                st.session_state["_git_branches_list"] = result.get("branches") or []
                st.session_state["_git_current_branch"] = result.get("current") or "N/A"
                st.success(f"Found {len(result.get('branches', []))} branches")
            else:
                st.error(f"Failed: {result}")

with qa_cols[3]:
    if st.button("Fetch Remote", use_container_width=True):
        with st.spinner("Fetching..."):
            result = _invoke(tools, "git_fetch", {"path": repo_path, "prune": True})
            if isinstance(result, dict) and result.get("ok"):
                st.success("Fetched from remote")
            else:
                st.error(f"Failed: {result}")

with qa_cols[4]:
    if st.button("Refresh All", use_container_width=True):
        with st.spinner("Refreshing..."):
            # Refresh status
            result = _invoke(tools, "git_status", {"path": repo_path})
            if isinstance(result, dict) and result.get("ok"):
                st.session_state["_git_status"] = result
            # Refresh commits
            result = _invoke(tools, "git_log", {"path": repo_path, "limit": 30})
            if isinstance(result, dict) and result.get("ok"):
                st.session_state["_git_commits_list"] = result.get("commits") or []
            # Refresh branches
            result = _invoke(tools, "git_branches", {"path": repo_path, "all_branches": True})
            if isinstance(result, dict) and result.get("ok"):
                st.session_state["_git_branches_list"] = result.get("branches") or []
                st.session_state["_git_current_branch"] = result.get("current") or "N/A"
            st.success("Refreshed!")
            st.rerun()

st.divider()

# Main content tabs
tabs = st.tabs(["Status", "Commits", "Branches", "Diff", "Remotes & Tags", "Tools & Debug"])

# --- STATUS TAB ---
with tabs[0]:
    st.markdown('<div class="git-card">', unsafe_allow_html=True)
    st.markdown("### Repository Status")

    col_refresh, col_info = st.columns([1, 2])
    with col_refresh:
        if st.button("Refresh Status", use_container_width=True):
            with st.spinner("Loading..."):
                result = _invoke(tools, "git_status", {"path": repo_path})
                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_git_status"] = result
                    st.success("Updated!")
                else:
                    st.error(f"Failed: {result}")

    status_data = st.session_state.get("_git_status", {})

    if status_data:
        is_clean = status_data.get("clean", False)
        files = status_data.get("files", [])

        if is_clean:
            st.success("Working tree is clean")
        else:
            # Categorize files
            modified = [f for f in files if f.get("status", "").strip().startswith("M")]
            added = [f for f in files if f.get("status", "").strip().startswith("A")]
            deleted = [f for f in files if f.get("status", "").strip().startswith("D")]
            untracked = [f for f in files if f.get("status", "").strip().startswith("?")]
            other = [f for f in files if f not in modified + added + deleted + untracked]

            st.markdown(
                f"""
                <div class="metric-grid">
                    <div class="metric-box">
                        <div class="metric-box-value" style="color: #f59e0b;">{len(modified)}</div>
                        <div class="metric-box-label">Modified</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-box-value" style="color: #059669;">{len(added)}</div>
                        <div class="metric-box-label">Added</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-box-value" style="color: #dc2626;">{len(deleted)}</div>
                        <div class="metric-box-label">Deleted</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-box-value" style="color: #6366f1;">{len(untracked)}</div>
                        <div class="metric-box-label">Untracked</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            st.markdown("**Changed Files:**")
            for f in files:
                status = f.get("status", "??").strip()
                filepath = f.get("file", "")
                status_class = "file-modified"
                if status.startswith("A"):
                    status_class = "file-added"
                elif status.startswith("D"):
                    status_class = "file-deleted"
                elif status.startswith("?"):
                    status_class = "file-untracked"
                st.markdown(f"<span class='{status_class}'>`{status}`</span> {filepath}", unsafe_allow_html=True)
    else:
        st.info("Click **Get Status** to load repository status.")

    st.markdown('</div>', unsafe_allow_html=True)

# --- COMMITS TAB ---
with tabs[1]:
    st.markdown('<div class="git-card">', unsafe_allow_html=True)
    st.markdown("### Commit History")

    col_limit, col_refresh = st.columns([2, 1])
    with col_limit:
        commit_limit = st.slider("Number of commits", 10, 100, 30)
    with col_refresh:
        if st.button("Refresh Commits", use_container_width=True):
            with st.spinner("Loading..."):
                result = _invoke(tools, "git_log", {"path": repo_path, "limit": commit_limit})
                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_git_commits_list"] = result.get("commits") or []
                    st.success("Updated!")
                else:
                    st.error(f"Failed: {result}")

    commits_list = st.session_state.get("_git_commits_list", [])

    if commits_list:
        for commit in commits_list:
            if isinstance(commit, dict):
                short_hash = commit.get("short_hash", "")
                subject = commit.get("subject", "")
                author = commit.get("author_name", "")
                timestamp = commit.get("timestamp", "")

                with st.expander(f"`{short_hash}` {subject[:60]}{'...' if len(subject) > 60 else ''}", expanded=False):
                    st.markdown(f"**Hash:** `{commit.get('hash', '')}`")
                    st.markdown(f"**Author:** {author} ({commit.get('author_email', '')})")
                    st.markdown(f"**Timestamp:** {timestamp}")
                    st.markdown(f"**Message:** {subject}")

                    if st.button(f"Show Details", key=f"show_{short_hash}"):
                        with st.spinner("Loading commit details..."):
                            result = _invoke(tools, "git_show", {"commit": short_hash, "path": repo_path, "stat": True})
                            if isinstance(result, dict) and result.get("ok"):
                                st.code(result.get("content", ""), language="diff")
                            else:
                                st.error(f"Failed: {result}")
            else:
                st.markdown(f"- {commit}")
    else:
        st.info("Click **List Commits** to load commit history.")

    st.markdown('</div>', unsafe_allow_html=True)

# --- BRANCHES TAB ---
with tabs[2]:
    st.markdown('<div class="git-card">', unsafe_allow_html=True)
    st.markdown("### Branches")

    col_opts, col_refresh = st.columns([2, 1])
    with col_opts:
        show_all = st.checkbox("Show all branches (including remote)", value=True)
    with col_refresh:
        if st.button("Refresh Branches", use_container_width=True):
            with st.spinner("Loading..."):
                result = _invoke(tools, "git_branches", {"path": repo_path, "all_branches": show_all})
                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_git_branches_list"] = result.get("branches") or []
                    st.session_state["_git_current_branch"] = result.get("current") or "N/A"
                    st.success("Updated!")
                else:
                    st.error(f"Failed: {result}")

    branches_list = st.session_state.get("_git_branches_list", [])
    current_branch = st.session_state.get("_git_current_branch", "N/A")

    if branches_list:
        st.markdown(f"**Current Branch:** <span class='branch-current'>{current_branch}</span>", unsafe_allow_html=True)
        st.divider()

        local_branches = [b for b in branches_list if not b.get("name", "").startswith("remotes/")]
        remote_branches = [b for b in branches_list if b.get("name", "").startswith("remotes/")]

        col_local, col_remote = st.columns(2)

        with col_local:
            st.markdown("**Local Branches:**")
            for branch in local_branches:
                name = branch.get("name", "")
                is_current = branch.get("current", False)
                if is_current:
                    st.markdown(f"* <span class='branch-current'>{name}</span> (current)", unsafe_allow_html=True)
                else:
                    if st.button(f"Checkout {name}", key=f"checkout_{name}"):
                        with st.spinner(f"Checking out {name}..."):
                            result = _invoke(tools, "git_checkout", {"ref": name, "path": repo_path})
                            if isinstance(result, dict) and result.get("ok"):
                                st.success(f"Checked out {name}")
                                st.session_state["_git_current_branch"] = name
                                st.rerun()
                            else:
                                st.error(f"Failed: {result}")

        with col_remote:
            st.markdown("**Remote Branches:**")
            for branch in remote_branches[:20]:  # Limit displayed
                name = branch.get("name", "")
                st.markdown(f"- <span class='branch-remote'>{name}</span>", unsafe_allow_html=True)
            if len(remote_branches) > 20:
                st.caption(f"... and {len(remote_branches) - 20} more")
    else:
        st.info("Click **List Branches** to load branch list.")

    st.markdown('</div>', unsafe_allow_html=True)

# --- DIFF TAB ---
with tabs[3]:
    st.markdown('<div class="git-card">', unsafe_allow_html=True)
    st.markdown("### View Diff")

    col_opts1, col_opts2 = st.columns(2)
    with col_opts1:
        staged_only = st.checkbox("Staged changes only", value=False)
    with col_opts2:
        stat_only = st.checkbox("Show stat only", value=False)

    file_filter = st.text_input("File path filter (optional)", placeholder="e.g., src/main.py")

    if st.button("Get Diff", use_container_width=True, type="primary"):
        with st.spinner("Loading diff..."):
            args = {
                "path": repo_path,
                "staged": staged_only,
                "stat": stat_only,
            }
            if file_filter.strip():
                args["file_path"] = file_filter.strip()

            result = _invoke(tools, "git_diff", args)
            if isinstance(result, dict) and result.get("ok"):
                diff_content = result.get("diff", "")
                if diff_content:
                    st.code(diff_content, language="diff")
                else:
                    st.info("No differences found")
            else:
                st.error(f"Failed: {result}")

    st.markdown('</div>', unsafe_allow_html=True)

# --- REMOTES & TAGS TAB ---
with tabs[4]:
    col_remotes, col_tags = st.columns(2)

    with col_remotes:
        st.markdown('<div class="git-card">', unsafe_allow_html=True)
        st.markdown("### Remotes")

        if st.button("List Remotes", use_container_width=True):
            with st.spinner("Loading..."):
                result = _invoke(tools, "git_remotes", {"path": repo_path})
                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_git_remotes_list"] = result.get("remotes") or []
                    st.success("Loaded!")
                else:
                    st.error(f"Failed: {result}")

        remotes_list = st.session_state.get("_git_remotes_list", [])
        if remotes_list:
            for remote in remotes_list:
                st.markdown(f"**{remote.get('name', '')}**")
                st.code(remote.get('url', ''))
        else:
            st.info("Click **List Remotes** to load.")

        st.markdown('</div>', unsafe_allow_html=True)

    with col_tags:
        st.markdown('<div class="git-card">', unsafe_allow_html=True)
        st.markdown("### Tags")

        if st.button("List Tags", use_container_width=True):
            with st.spinner("Loading..."):
                result = _invoke(tools, "git_tags", {"path": repo_path, "limit": 30})
                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_git_tags_list"] = result.get("tags") or []
                    st.success("Loaded!")
                else:
                    st.error(f"Failed: {result}")

        tags_list = st.session_state.get("_git_tags_list", [])
        if tags_list:
            for tag in tags_list:
                st.markdown(f"- `{tag}`")
        else:
            st.info("Click **List Tags** to load.")

        st.markdown('</div>', unsafe_allow_html=True)

# --- TOOLS & DEBUG TAB ---
with tabs[5]:
    st.markdown('<div class="git-card">', unsafe_allow_html=True)
    st.markdown("### Available MCP Tools")

    col_info, col_refresh = st.columns([3, 1])

    with col_info:
        st.markdown(f"**Loaded Tools:** {len(tools)}")

    with col_refresh:
        if st.button("Reload Tools", use_container_width=True):
            tools = _get_git_tools(force_reload=True)
            st.success("Tools reloaded!")
            st.rerun()

    # List all available tools
    with st.expander("Show All Tool Names", expanded=False):
        if tools:
            for idx, tool in enumerate(tools, 1):
                tool_name = tool.get("name", "unknown") if isinstance(tool, dict) else str(tool)
                st.markdown(f"{idx}. `{tool_name}`")
        else:
            st.info("No tools available")

    st.divider()

    # Health check
    st.markdown("### Git Health Check")

    if st.button("Run Health Check", use_container_width=True):
        with st.spinner("Checking git availability..."):
            health_result = _invoke(tools, "git_health_check", {})
            if isinstance(health_result, dict):
                if health_result.get("ok"):
                    st.success("Git is available")
                else:
                    st.error("Git reported issues")
                st.json(health_result)
            else:
                st.error("Unexpected health check response format")
                st.code(str(health_result))

    st.divider()

    # Clone repository
    st.markdown("### Clone Repository")

    clone_url = st.text_input("Repository URL", placeholder="https://github.com/user/repo.git")
    clone_dest = st.text_input("Destination path", placeholder="/path/to/destination")
    clone_branch = st.text_input("Branch (optional)", placeholder="main")
    clone_depth = st.number_input("Depth (0 for full clone)", min_value=0, value=0)

    if st.button("Clone Repository", use_container_width=True, type="primary"):
        if clone_url.strip() and clone_dest.strip():
            with st.spinner("Cloning..."):
                args = {
                    "url": clone_url.strip(),
                    "dest": clone_dest.strip(),
                }
                if clone_branch.strip():
                    args["branch"] = clone_branch.strip()
                if clone_depth > 0:
                    args["depth"] = clone_depth

                result = _invoke(tools, "git_clone", args)
                if isinstance(result, dict) and result.get("ok"):
                    st.success(f"Cloned to {clone_dest}")
                else:
                    st.error(f"Failed: {result}")
        else:
            st.warning("Please provide both URL and destination path")

    st.markdown('</div>', unsafe_allow_html=True)

# Footer
st.divider()
st.caption(
    "**Tip:** Use the sidebar to configure the repository path. "
    "The Git MCP server uses the git CLI, so make sure git is installed."
)
