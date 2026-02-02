"""Git Repository Insights - Comprehensive Git Analytics & Intelligence."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from src.admin_config import load_admin_config
from src.mcp_client import get_mcp_client, get_server_url
from src.mcp_health import add_mcp_status_styles
from src.streamlit_config import get_app_config
from src.theme import set_theme


set_theme(page_title="Git Insights", page_icon="📊")

admin = load_admin_config()
if not admin.is_mcp_enabled("git", default=True):
    st.info("Git MCP is disabled by Admin.")
    st.stop()

add_mcp_status_styles()

# ─────────────────────────────────────────────────────────────────────────────
# ENHANCED STYLES
# ─────────────────────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    /* Hero Section */
    .git-hero {
        background: linear-gradient(135deg, #f97316 0%, #ea580c 50%, #c2410c 100%);
        background-size: 200% 200%;
        animation: gradient-shift 8s ease infinite;
        border-radius: 24px;
        padding: 2rem 2.5rem;
        margin-bottom: 1.5rem;
        color: white;
        box-shadow: 0 12px 48px rgba(249, 115, 22, 0.35);
        position: relative;
        overflow: hidden;
    }
    .git-hero::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        background: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.08'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
        opacity: 0.5;
    }
    @keyframes gradient-shift {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    .git-hero h1 {
        font-size: 2.4rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        position: relative;
        text-shadow: 0 2px 4px rgba(0,0,0,0.15);
    }
    .git-hero p {
        margin: 0;
        opacity: 0.95;
        font-size: 1.1rem;
        position: relative;
    }
    .hero-stats {
        display: flex;
        gap: 1.5rem;
        margin-top: 1.25rem;
        position: relative;
        flex-wrap: wrap;
    }
    .hero-stat {
        background: rgba(255,255,255,0.18);
        backdrop-filter: blur(10px);
        padding: 0.6rem 1.2rem;
        border-radius: 12px;
        font-size: 0.95rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .hero-stat strong {
        font-size: 1.15rem;
    }

    /* Insight Cards */
    .insight-card {
        background: linear-gradient(145deg, #ffffff, #fafafa);
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid #e5e7eb;
        box-shadow: 0 4px 20px rgba(0,0,0,0.06);
        margin-bottom: 1rem;
        transition: all 0.2s ease;
    }
    .insight-card:hover {
        box-shadow: 0 8px 32px rgba(0,0,0,0.1);
        transform: translateY(-2px);
    }
    .insight-card h3 {
        font-size: 1.15rem;
        font-weight: 700;
        margin: 0 0 1rem 0;
        color: #1f2937;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    /* Issue Cards */
    .issue-card {
        border-radius: 12px;
        padding: 1rem 1.25rem;
        margin: 0.5rem 0;
        border-left: 4px solid;
    }
    .issue-critical {
        background: linear-gradient(135deg, #fef2f2, #fee2e2);
        border-color: #ef4444;
    }
    .issue-warning {
        background: linear-gradient(135deg, #fffbeb, #fef3c7);
        border-color: #f59e0b;
    }
    .issue-info {
        background: linear-gradient(135deg, #eff6ff, #dbeafe);
        border-color: #3b82f6;
    }
    .issue-success {
        background: linear-gradient(135deg, #f0fdf4, #dcfce7);
        border-color: #22c55e;
    }

    /* Priority Badge */
    .priority-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.3rem;
        padding: 0.25rem 0.75rem;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .priority-high { background: #fee2e2; color: #dc2626; }
    .priority-medium { background: #fef3c7; color: #d97706; }
    .priority-low { background: #dbeafe; color: #2563eb; }

    /* Metric Grid */
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 1rem;
        margin: 1rem 0;
    }
    .metric-box {
        background: linear-gradient(145deg, #f8fafc, #f1f5f9);
        border-radius: 14px;
        padding: 1.25rem 1rem;
        text-align: center;
        border: 1px solid #e2e8f0;
        transition: all 0.2s;
    }
    .metric-box:hover {
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
    }
    .metric-value {
        font-size: 2rem;
        font-weight: 800;
        color: #0f172a;
        line-height: 1.2;
    }
    .metric-label {
        font-size: 0.85rem;
        color: #64748b;
        margin-top: 0.35rem;
    }
    .metric-trend {
        font-size: 0.75rem;
        margin-top: 0.25rem;
    }
    .trend-up { color: #22c55e; }
    .trend-down { color: #ef4444; }
    .trend-neutral { color: #64748b; }

    /* Hotspot Bar */
    .hotspot-bar {
        height: 8px;
        border-radius: 4px;
        background: #e5e7eb;
        overflow: hidden;
        margin: 0.5rem 0;
    }
    .hotspot-fill {
        height: 100%;
        border-radius: 4px;
        transition: width 0.3s ease;
    }
    .hotspot-critical { background: linear-gradient(90deg, #ef4444, #dc2626); }
    .hotspot-high { background: linear-gradient(90deg, #f97316, #ea580c); }
    .hotspot-medium { background: linear-gradient(90deg, #eab308, #ca8a04); }
    .hotspot-low { background: linear-gradient(90deg, #22c55e, #16a34a); }

    /* Timeline */
    .commit-timeline {
        position: relative;
        padding-left: 2rem;
    }
    .commit-timeline::before {
        content: '';
        position: absolute;
        left: 0.5rem;
        top: 0;
        bottom: 0;
        width: 3px;
        background: linear-gradient(to bottom, #f97316, #ea580c, #c2410c);
        border-radius: 2px;
    }
    .timeline-item {
        position: relative;
        padding: 0.75rem 0 0.75rem 1rem;
        border-bottom: 1px solid #f1f5f9;
    }
    .timeline-item:last-child { border-bottom: none; }
    .timeline-item::before {
        content: '';
        position: absolute;
        left: -1.6rem;
        top: 1rem;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: #f97316;
        border: 3px solid white;
        box-shadow: 0 0 0 2px #f97316;
    }

    /* Branch Health */
    .branch-health {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.5rem 0;
    }
    .health-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
    }
    .health-healthy { background: #22c55e; }
    .health-stale { background: #f59e0b; }
    .health-critical { background: #ef4444; }

    /* Author Avatar */
    .author-avatar {
        width: 36px;
        height: 36px;
        border-radius: 50%;
        background: linear-gradient(135deg, #f97316, #ea580c);
        display: flex;
        align-items: center;
        justify-content: center;
        color: white;
        font-weight: 700;
        font-size: 0.9rem;
    }

    /* File Status */
    .file-modified { color: #f59e0b; }
    .file-added { color: #22c55e; }
    .file-deleted { color: #ef4444; }
    .file-untracked { color: #6366f1; }

    /* Quick Action Button */
    .quick-action-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        gap: 0.75rem;
        margin: 1rem 0;
    }

    /* Summary Box */
    .summary-box {
        background: linear-gradient(135deg, rgba(249,115,22,0.08), rgba(234,88,12,0.05));
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid rgba(249,115,22,0.2);
        margin: 1rem 0;
    }
    .summary-box h4 {
        color: #ea580c;
        font-weight: 700;
        margin: 0 0 0.75rem 0;
    }

    /* Contribution Graph */
    .contrib-cell {
        width: 12px;
        height: 12px;
        border-radius: 2px;
        display: inline-block;
        margin: 1px;
    }
    .contrib-0 { background: #ebedf0; }
    .contrib-1 { background: #9be9a8; }
    .contrib-2 { background: #40c463; }
    .contrib-3 { background: #30a14e; }
    .contrib-4 { background: #216e39; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

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


def _parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse various timestamp formats."""
    if not ts:
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%a %b %d %H:%M:%S %Y %z",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(ts.strip(), fmt)
        except ValueError:
            continue
    return None


def _days_ago(dt: Optional[datetime]) -> int:
    """Calculate days since a datetime."""
    if not dt:
        return 999
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    return max(0, (now - dt).days)


def _get_initials(name: str) -> str:
    """Get initials from a name."""
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper() if name else "??"


def _analyze_commits(commits: List[Dict]) -> Dict[str, Any]:
    """Analyze commit patterns and return insights."""
    if not commits:
        return {}

    author_counts = Counter()
    daily_counts = Counter()
    hourly_counts = Counter()
    weekday_counts = Counter()
    files_changed = Counter()
    commit_sizes = []

    for commit in commits:
        # Author analysis
        author = commit.get("author_name", "Unknown")
        author_counts[author] += 1

        # Time analysis
        ts = _parse_timestamp(commit.get("timestamp", ""))
        if ts:
            daily_counts[ts.strftime("%Y-%m-%d")] += 1
            hourly_counts[ts.hour] += 1
            weekday_counts[ts.strftime("%A")] += 1

        # Size analysis (if available)
        stats = commit.get("stats", {})
        if stats:
            additions = stats.get("additions", 0)
            deletions = stats.get("deletions", 0)
            commit_sizes.append(additions + deletions)

    # Calculate averages and trends
    total_commits = len(commits)
    avg_commits_per_day = total_commits / max(len(daily_counts), 1)

    # Most active times
    peak_hour = max(hourly_counts, key=hourly_counts.get) if hourly_counts else 12
    peak_day = max(weekday_counts, key=weekday_counts.get) if weekday_counts else "Monday"

    return {
        "total_commits": total_commits,
        "unique_authors": len(author_counts),
        "author_counts": dict(author_counts.most_common(10)),
        "daily_counts": dict(daily_counts),
        "hourly_distribution": dict(hourly_counts),
        "weekday_distribution": dict(weekday_counts),
        "avg_commits_per_day": round(avg_commits_per_day, 1),
        "peak_hour": peak_hour,
        "peak_day": peak_day,
        "avg_commit_size": round(sum(commit_sizes) / len(commit_sizes), 0) if commit_sizes else 0,
    }


def _analyze_branches(branches: List[Dict], commits: List[Dict]) -> Dict[str, Any]:
    """Analyze branch health and patterns."""
    if not branches:
        return {}

    local_branches = [b for b in branches if not b.get("name", "").startswith("remotes/")]
    remote_branches = [b for b in branches if b.get("name", "").startswith("remotes/")]

    # Identify stale branches (no commits in 30+ days based on available data)
    stale_branches = []
    healthy_branches = []

    for branch in local_branches:
        name = branch.get("name", "")
        last_commit_date = branch.get("last_commit_date")
        if last_commit_date:
            days = _days_ago(_parse_timestamp(last_commit_date))
            if days > 30:
                stale_branches.append({"name": name, "days_inactive": days})
            else:
                healthy_branches.append({"name": name, "days_inactive": days})
        else:
            healthy_branches.append({"name": name, "days_inactive": 0})

    return {
        "total_branches": len(branches),
        "local_count": len(local_branches),
        "remote_count": len(remote_branches),
        "stale_branches": stale_branches,
        "healthy_branches": healthy_branches,
    }


def _detect_issues(status: Dict, branches: List, commits: List) -> List[Dict]:
    """Detect potential issues in the repository."""
    issues = []

    # Check for uncommitted changes
    files = status.get("files", [])
    if files:
        modified = len([f for f in files if f.get("status", "").startswith("M")])
        untracked = len([f for f in files if f.get("status", "").startswith("?")])

        if modified > 10:
            issues.append({
                "type": "warning",
                "title": "Many Uncommitted Changes",
                "description": f"You have {modified} modified files. Consider committing or stashing.",
                "priority": "medium",
            })

        if untracked > 20:
            issues.append({
                "type": "warning",
                "title": "Many Untracked Files",
                "description": f"{untracked} untracked files. Update .gitignore or add them.",
                "priority": "low",
            })

    # Check for stale branches
    local_branches = [b for b in branches if not b.get("name", "").startswith("remotes/")]
    if len(local_branches) > 10:
        issues.append({
            "type": "info",
            "title": "Many Local Branches",
            "description": f"{len(local_branches)} local branches. Consider cleaning up merged branches.",
            "priority": "low",
        })

    # Check commit frequency
    if commits:
        recent_commits = [c for c in commits if _days_ago(_parse_timestamp(c.get("timestamp", ""))) < 7]
        if len(recent_commits) == 0:
            issues.append({
                "type": "warning",
                "title": "No Recent Commits",
                "description": "No commits in the last 7 days. Project may be stale.",
                "priority": "medium",
            })

    # Check for large files or sensitive patterns
    for f in files:
        filepath = f.get("file", "")
        if any(pattern in filepath.lower() for pattern in [".env", "credentials", "secret", "password", "key.pem"]):
            issues.append({
                "type": "critical",
                "title": "Potential Sensitive File",
                "description": f"File '{filepath}' may contain sensitive data. Ensure it's in .gitignore.",
                "priority": "high",
            })

    if not issues:
        issues.append({
            "type": "success",
            "title": "Repository Health: Good",
            "description": "No major issues detected. Keep up the good work!",
            "priority": "none",
        })

    return issues


def _generate_recommendations(analysis: Dict, issues: List) -> List[Dict]:
    """Generate actionable recommendations."""
    recommendations = []

    # Based on commit patterns
    if analysis.get("avg_commits_per_day", 0) < 0.5:
        recommendations.append({
            "title": "Increase Commit Frequency",
            "description": "Small, frequent commits are easier to review and debug.",
            "priority": "medium",
            "category": "workflow",
        })

    # Based on author distribution
    author_counts = analysis.get("author_counts", {})
    if len(author_counts) == 1:
        recommendations.append({
            "title": "Code Review Process",
            "description": "Single contributor detected. Consider adding peer reviews.",
            "priority": "low",
            "category": "collaboration",
        })

    # Based on issues
    critical_issues = [i for i in issues if i.get("priority") == "high"]
    if critical_issues:
        recommendations.append({
            "title": "Address Security Concerns",
            "description": "Review and fix critical issues before continuing development.",
            "priority": "high",
            "category": "security",
        })

    return recommendations


def _render_contribution_graph(daily_counts: Dict[str, int], days: int = 90) -> str:
    """Render a GitHub-style contribution graph."""
    if not daily_counts:
        return ""

    max_count = max(daily_counts.values()) if daily_counts else 1
    cells = []

    end_date = datetime.now()
    for i in range(days, -1, -1):
        date = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
        count = daily_counts.get(date, 0)

        # Calculate intensity level (0-4)
        if count == 0:
            level = 0
        elif count <= max_count * 0.25:
            level = 1
        elif count <= max_count * 0.5:
            level = 2
        elif count <= max_count * 0.75:
            level = 3
        else:
            level = 4

        cells.append(f'<span class="contrib-cell contrib-{level}" title="{date}: {count} commits"></span>')

    return "".join(cells)


# ─────────────────────────────────────────────────────────────────────────────
# STATE INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

cfg = get_app_config()
git_url = get_server_url("git")

# Invalidate cached tools if the target URL changes
if st.session_state.get("_git_tools_sig") != git_url:
    st.session_state.pop("_git_tools", None)
    st.session_state["_git_tools_sig"] = git_url

# Default to project repo path
if "_git_repo_path" not in st.session_state:
    st.session_state["_git_repo_path"] = "/repos/project"


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚙️ Configuration")

    # Repository selection
    st.markdown("#### Repository")
    repo_options = [
        "/repos/project",  # The mounted project folder
        cfg.git.repo_path or "/repos",
    ]
    # Add custom option
    repo_path = st.selectbox(
        "Select Repository",
        options=repo_options + ["Custom..."],
        index=0,
        help="Select the repository to analyze",
    )

    if repo_path == "Custom...":
        repo_path = st.text_input(
            "Custom Path",
            value=st.session_state.get("_git_repo_path", ""),
            placeholder="/path/to/repo",
        )

    st.session_state["_git_repo_path"] = repo_path

    st.divider()

    # Quick Actions
    st.markdown("#### Quick Actions")

    if st.button("🔄 Refresh All Data", use_container_width=True, type="primary"):
        st.session_state["_git_refresh_all"] = True
        st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("📥 Fetch", use_container_width=True):
            st.session_state["_git_action"] = "fetch"
            st.rerun()
    with col2:
        if st.button("🔧 Tools", use_container_width=True):
            st.session_state["_git_show_tools"] = True

    st.divider()

    # Quick Stats
    st.markdown("#### 📊 Quick Stats")

    commits_list = st.session_state.get("_git_commits_list", [])
    branches_list = st.session_state.get("_git_branches_list", [])
    status_data = st.session_state.get("_git_status", {})

    current_branch = st.session_state.get("_git_current_branch", "—")

    st.metric("Current Branch", current_branch[:20] + "..." if len(current_branch) > 20 else current_branch)

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        st.metric("Commits", len(commits_list))
    with col_s2:
        st.metric("Branches", len(branches_list))

    files_changed = len(status_data.get("files", []))
    st.metric("Changed Files", files_changed)

    st.divider()

    # Connection Info
    with st.expander("🔌 Connection", expanded=False):
        st.code(git_url, language=None)
        st.caption("Git MCP Server")


# ─────────────────────────────────────────────────────────────────────────────
# TOOL LOADING
# ─────────────────────────────────────────────────────────────────────────────

# Auto-load tools
if "_git_tools" not in st.session_state:
    try:
        with st.spinner("Connecting to Git MCP..."):
            _get_git_tools(force_reload=True)
    except Exception as exc:
        st.error(f"Failed to connect to Git MCP: {exc}")
        st.info(
            "**Troubleshooting:**\n"
            "- Ensure the git-mcp container is running\n"
            "- Check docker-compose logs for errors\n"
            "- Verify GIT_MCP_URL configuration"
        )
        st.stop()

tools = st.session_state.get("_git_tools")
if not tools:
    st.error("Git MCP tools not available")
    st.stop()

repo_path = st.session_state.get("_git_repo_path", "/repos/project")


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

# Handle refresh all
if st.session_state.get("_git_refresh_all"):
    st.session_state.pop("_git_refresh_all", None)
    with st.spinner("Refreshing all data..."):
        # Status
        result = _invoke(tools, "git_status", {"path": repo_path})
        if isinstance(result, dict) and result.get("ok"):
            st.session_state["_git_status"] = result

        # Commits (get more for analysis)
        result = _invoke(tools, "git_log", {"path": repo_path, "limit": 200})
        if isinstance(result, dict) and result.get("ok"):
            st.session_state["_git_commits_list"] = result.get("commits") or []

        # Branches
        result = _invoke(tools, "git_branches", {"path": repo_path, "all_branches": True})
        if isinstance(result, dict) and result.get("ok"):
            st.session_state["_git_branches_list"] = result.get("branches") or []
            st.session_state["_git_current_branch"] = result.get("current") or "N/A"

        # Remotes
        result = _invoke(tools, "git_remotes", {"path": repo_path})
        if isinstance(result, dict) and result.get("ok"):
            st.session_state["_git_remotes_list"] = result.get("remotes") or []

        # Tags
        result = _invoke(tools, "git_tags", {"path": repo_path, "limit": 50})
        if isinstance(result, dict) and result.get("ok"):
            st.session_state["_git_tags_list"] = result.get("tags") or []

# Handle fetch action
if st.session_state.get("_git_action") == "fetch":
    st.session_state.pop("_git_action", None)
    with st.spinner("Fetching from remote..."):
        result = _invoke(tools, "git_fetch", {"path": repo_path, "prune": True})
        if isinstance(result, dict) and result.get("ok"):
            st.success("Fetched from remote!")
        else:
            st.error(f"Fetch failed: {result}")

# Auto-load if no data
if "_git_status" not in st.session_state:
    st.session_state["_git_refresh_all"] = True
    st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# HERO SECTION
# ─────────────────────────────────────────────────────────────────────────────

commits_list = st.session_state.get("_git_commits_list", [])
branches_list = st.session_state.get("_git_branches_list", [])
status_data = st.session_state.get("_git_status", {})
remotes_list = st.session_state.get("_git_remotes_list", [])
tags_list = st.session_state.get("_git_tags_list", [])

current_branch = st.session_state.get("_git_current_branch", "main")
is_clean = status_data.get("clean", True)
changed_files = len(status_data.get("files", []))

# Analyze data
commit_analysis = _analyze_commits(commits_list)
branch_analysis = _analyze_branches(branches_list, commits_list)
issues = _detect_issues(status_data, branches_list, commits_list)
recommendations = _generate_recommendations(commit_analysis, issues)

# Calculate health score
health_score = 100
for issue in issues:
    if issue.get("priority") == "high":
        health_score -= 30
    elif issue.get("priority") == "medium":
        health_score -= 15
    elif issue.get("priority") == "low":
        health_score -= 5
health_score = max(0, min(100, health_score))

st.markdown(
    f"""
    <div class="git-hero">
        <h1>📊 Git Repository Insights</h1>
        <p>Comprehensive analytics and intelligence for your Git repository</p>
        <div class="hero-stats">
            <div class="hero-stat">
                <span>🌿</span>
                <span><strong>{current_branch}</strong></span>
            </div>
            <div class="hero-stat">
                <span>{'✅' if is_clean else '⚠️'}</span>
                <span>{'Clean' if is_clean else f'{changed_files} changes'}</span>
            </div>
            <div class="hero-stat">
                <span>📝</span>
                <span><strong>{len(commits_list)}</strong> commits</span>
            </div>
            <div class="hero-stat">
                <span>🔀</span>
                <span><strong>{branch_analysis.get('local_count', 0)}</strong> branches</span>
            </div>
            <div class="hero-stat">
                <span>👥</span>
                <span><strong>{commit_analysis.get('unique_authors', 0)}</strong> contributors</span>
            </div>
            <div class="hero-stat">
                <span>💚</span>
                <span>Health: <strong>{health_score}%</strong></span>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN TABS
# ─────────────────────────────────────────────────────────────────────────────

tab_overview, tab_commits, tab_branches, tab_issues, tab_actions, tab_tools = st.tabs([
    "📈 Overview", "📝 Commits", "🌿 Branches", "⚠️ Issues & Recommendations", "⚡ Actions", "🔧 Tools"
])


# ─────────────────────────────────────────────────────────────────────────────
# OVERVIEW TAB
# ─────────────────────────────────────────────────────────────────────────────

with tab_overview:
    # Key Metrics
    st.markdown("### 📊 Key Metrics")

    m1, m2, m3, m4, m5, m6 = st.columns(6)

    with m1:
        st.markdown(
            f"""
            <div class="metric-box">
                <div class="metric-value">{len(commits_list)}</div>
                <div class="metric-label">Total Commits</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with m2:
        st.markdown(
            f"""
            <div class="metric-box">
                <div class="metric-value">{commit_analysis.get('unique_authors', 0)}</div>
                <div class="metric-label">Contributors</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with m3:
        st.markdown(
            f"""
            <div class="metric-box">
                <div class="metric-value">{branch_analysis.get('local_count', 0)}</div>
                <div class="metric-label">Branches</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with m4:
        st.markdown(
            f"""
            <div class="metric-box">
                <div class="metric-value">{len(tags_list)}</div>
                <div class="metric-label">Tags</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with m5:
        avg_per_day = commit_analysis.get('avg_commits_per_day', 0)
        st.markdown(
            f"""
            <div class="metric-box">
                <div class="metric-value">{avg_per_day}</div>
                <div class="metric-label">Commits/Day</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with m6:
        st.markdown(
            f"""
            <div class="metric-box">
                <div class="metric-value" style="color: {'#22c55e' if health_score >= 80 else '#f59e0b' if health_score >= 50 else '#ef4444'}">{health_score}%</div>
                <div class="metric-label">Health Score</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()

    # Two column layout
    col_left, col_right = st.columns([3, 2])

    with col_left:
        # Repository Summary
        st.markdown("### 📋 Repository Summary")

        st.markdown(
            f"""
            <div class="summary-box">
                <h4>🔍 Quick Overview</h4>
                <p>
                    You're on branch <strong>{current_branch}</strong> with
                    <strong>{len(commits_list)}</strong> commits analyzed.
                    {'The working tree is <strong style="color:#22c55e">clean</strong>.' if is_clean else f'There are <strong style="color:#f59e0b">{changed_files} uncommitted changes</strong>.'}
                </p>
                <p>
                    <strong>{commit_analysis.get('unique_authors', 0)}</strong> contributors have worked on this repository.
                    Peak activity is on <strong>{commit_analysis.get('peak_day', 'N/A')}</strong> at <strong>{commit_analysis.get('peak_hour', 0):02d}:00</strong>.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Contribution Activity (GitHub-style graph)
        st.markdown("### 📅 Contribution Activity")
        daily_counts = commit_analysis.get("daily_counts", {})
        if daily_counts:
            graph_html = _render_contribution_graph(daily_counts, days=90)
            st.markdown(
                f"""
                <div class="insight-card">
                    <h3>📊 Last 90 Days</h3>
                    <div style="line-height: 0; font-size: 0;">
                        {graph_html}
                    </div>
                    <div style="margin-top: 1rem; display: flex; align-items: center; gap: 0.5rem; font-size: 0.8rem; color: #64748b;">
                        <span>Less</span>
                        <span class="contrib-cell contrib-0"></span>
                        <span class="contrib-cell contrib-1"></span>
                        <span class="contrib-cell contrib-2"></span>
                        <span class="contrib-cell contrib-3"></span>
                        <span class="contrib-cell contrib-4"></span>
                        <span>More</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # Recent Activity Timeline
        st.markdown("### 🕒 Recent Activity")

        st.markdown('<div class="insight-card">', unsafe_allow_html=True)
        st.markdown('<div class="commit-timeline">', unsafe_allow_html=True)

        for commit in commits_list[:10]:
            short_hash = commit.get("short_hash", "")[:7]
            subject = commit.get("subject", "")[:60]
            author = commit.get("author_name", "Unknown")
            ts = commit.get("timestamp", "")

            st.markdown(
                f"""
                <div class="timeline-item">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <code style="background: #f1f5f9; padding: 0.2rem 0.5rem; border-radius: 4px; color: #ea580c; font-size: 0.85rem;">{short_hash}</code>
                            <span style="margin-left: 0.5rem; color: #1f2937;">{subject}{'...' if len(commit.get("subject", "")) > 60 else ''}</span>
                        </div>
                    </div>
                    <div style="font-size: 0.8rem; color: #64748b; margin-top: 0.25rem;">
                        {author} • {ts[:16] if ts else 'Unknown time'}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown('</div></div>', unsafe_allow_html=True)

    with col_right:
        # Top Contributors
        st.markdown("### 👥 Top Contributors")

        st.markdown('<div class="insight-card">', unsafe_allow_html=True)

        author_counts = commit_analysis.get("author_counts", {})
        total_commits = sum(author_counts.values()) if author_counts else 1

        for author, count in list(author_counts.items())[:8]:
            pct = (count / total_commits) * 100
            initials = _get_initials(author)

            st.markdown(
                f"""
                <div style="display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 0; border-bottom: 1px solid #f1f5f9;">
                    <div class="author-avatar">{initials}</div>
                    <div style="flex: 1;">
                        <div style="font-weight: 600; color: #1f2937;">{author[:20]}{'...' if len(author) > 20 else ''}</div>
                        <div style="font-size: 0.8rem; color: #64748b;">{count} commits ({pct:.1f}%)</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown('</div>', unsafe_allow_html=True)

        # Working Tree Status
        st.markdown("### 📁 Working Tree")

        st.markdown('<div class="insight-card">', unsafe_allow_html=True)

        if is_clean:
            st.success("✅ Working tree is clean")
        else:
            files = status_data.get("files", [])
            modified = [f for f in files if f.get("status", "").strip().startswith("M")]
            added = [f for f in files if f.get("status", "").strip().startswith("A")]
            deleted = [f for f in files if f.get("status", "").strip().startswith("D")]
            untracked = [f for f in files if f.get("status", "").strip().startswith("?")]

            st.markdown(
                f"""
                <div class="metric-grid">
                    <div class="metric-box">
                        <div class="metric-value" style="color: #f59e0b; font-size: 1.5rem;">{len(modified)}</div>
                        <div class="metric-label">Modified</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value" style="color: #22c55e; font-size: 1.5rem;">{len(added)}</div>
                        <div class="metric-label">Added</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value" style="color: #ef4444; font-size: 1.5rem;">{len(deleted)}</div>
                        <div class="metric-label">Deleted</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value" style="color: #6366f1; font-size: 1.5rem;">{len(untracked)}</div>
                        <div class="metric-label">Untracked</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            with st.expander("View Changed Files", expanded=False):
                for f in files[:20]:
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

                if len(files) > 20:
                    st.caption(f"... and {len(files) - 20} more files")

        st.markdown('</div>', unsafe_allow_html=True)

        # Remotes
        st.markdown("### 🌐 Remotes")

        st.markdown('<div class="insight-card">', unsafe_allow_html=True)

        if remotes_list:
            for remote in remotes_list:
                st.markdown(f"**{remote.get('name', '')}**")
                st.code(remote.get('url', ''), language=None)
        else:
            st.info("No remotes configured")

        st.markdown('</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# COMMITS TAB
# ─────────────────────────────────────────────────────────────────────────────

with tab_commits:
    st.markdown("### 📝 Commit History & Analysis")

    col_ctrl1, col_ctrl2 = st.columns([2, 1])
    with col_ctrl1:
        commit_limit = st.slider("Number of commits to show", 10, 200, 50, key="commit_limit_slider")
    with col_ctrl2:
        if st.button("🔄 Refresh Commits", use_container_width=True):
            with st.spinner("Loading commits..."):
                result = _invoke(tools, "git_log", {"path": repo_path, "limit": commit_limit})
                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_git_commits_list"] = result.get("commits") or []
                    st.success("Updated!")
                    st.rerun()

    # Commit Analysis Charts
    st.markdown("#### 📊 Commit Patterns")

    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        # Weekday distribution
        weekday_dist = commit_analysis.get("weekday_distribution", {})
        if weekday_dist:
            st.markdown('<div class="insight-card">', unsafe_allow_html=True)
            st.markdown("**Commits by Day of Week**")

            days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            max_count = max(weekday_dist.values()) if weekday_dist else 1

            for day in days_order:
                count = weekday_dist.get(day, 0)
                pct = (count / max_count) * 100 if max_count > 0 else 0
                st.markdown(
                    f"""
                    <div style="display: flex; align-items: center; gap: 0.5rem; margin: 0.25rem 0;">
                        <span style="width: 80px; font-size: 0.85rem; color: #64748b;">{day[:3]}</span>
                        <div style="flex: 1; height: 20px; background: #e5e7eb; border-radius: 4px; overflow: hidden;">
                            <div style="height: 100%; width: {pct}%; background: linear-gradient(90deg, #f97316, #ea580c); border-radius: 4px;"></div>
                        </div>
                        <span style="width: 30px; font-size: 0.85rem; color: #1f2937; text-align: right;">{count}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            st.markdown('</div>', unsafe_allow_html=True)

    with col_chart2:
        # Hourly distribution
        hourly_dist = commit_analysis.get("hourly_distribution", {})
        if hourly_dist:
            st.markdown('<div class="insight-card">', unsafe_allow_html=True)
            st.markdown("**Commits by Hour**")

            max_count = max(hourly_dist.values()) if hourly_dist else 1

            # Group into 6-hour blocks
            blocks = {
                "Night (0-5)": sum(hourly_dist.get(h, 0) for h in range(0, 6)),
                "Morning (6-11)": sum(hourly_dist.get(h, 0) for h in range(6, 12)),
                "Afternoon (12-17)": sum(hourly_dist.get(h, 0) for h in range(12, 18)),
                "Evening (18-23)": sum(hourly_dist.get(h, 0) for h in range(18, 24)),
            }

            block_max = max(blocks.values()) if blocks else 1

            for block, count in blocks.items():
                pct = (count / block_max) * 100 if block_max > 0 else 0
                st.markdown(
                    f"""
                    <div style="display: flex; align-items: center; gap: 0.5rem; margin: 0.5rem 0;">
                        <span style="width: 120px; font-size: 0.85rem; color: #64748b;">{block}</span>
                        <div style="flex: 1; height: 20px; background: #e5e7eb; border-radius: 4px; overflow: hidden;">
                            <div style="height: 100%; width: {pct}%; background: linear-gradient(90deg, #6366f1, #4f46e5); border-radius: 4px;"></div>
                        </div>
                        <span style="width: 30px; font-size: 0.85rem; color: #1f2937; text-align: right;">{count}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            st.markdown('</div>', unsafe_allow_html=True)

    st.divider()

    # Commit List
    st.markdown("#### 📋 Commit List")

    search_query = st.text_input("🔍 Search commits", placeholder="Filter by message, author, or hash...")

    filtered_commits = commits_list
    if search_query:
        query = search_query.lower()
        filtered_commits = [
            c for c in commits_list
            if query in c.get("subject", "").lower()
            or query in c.get("author_name", "").lower()
            or query in c.get("hash", "").lower()
            or query in c.get("short_hash", "").lower()
        ]

    st.caption(f"Showing {len(filtered_commits)} of {len(commits_list)} commits")

    for commit in filtered_commits[:commit_limit]:
        short_hash = commit.get("short_hash", "")[:7]
        full_hash = commit.get("hash", "")
        subject = commit.get("subject", "")
        author = commit.get("author_name", "Unknown")
        email = commit.get("author_email", "")
        ts = commit.get("timestamp", "")

        with st.expander(f"`{short_hash}` {subject[:70]}{'...' if len(subject) > 70 else ''}", expanded=False):
            col_info, col_action = st.columns([3, 1])

            with col_info:
                st.markdown(f"**Full Hash:** `{full_hash}`")
                st.markdown(f"**Author:** {author} ({email})")
                st.markdown(f"**Date:** {ts}")
                st.markdown(f"**Message:** {subject}")

            with col_action:
                if st.button("📄 Show Diff", key=f"diff_{short_hash}"):
                    with st.spinner("Loading..."):
                        result = _invoke(tools, "git_show", {"commit": short_hash, "path": repo_path, "stat": True})
                        if isinstance(result, dict) and result.get("ok"):
                            st.code(result.get("content", "")[:5000], language="diff")
                        else:
                            st.error(f"Failed: {result}")


# ─────────────────────────────────────────────────────────────────────────────
# BRANCHES TAB
# ─────────────────────────────────────────────────────────────────────────────

with tab_branches:
    st.markdown("### 🌿 Branch Management & Health")

    col_ctrl, col_refresh = st.columns([3, 1])
    with col_ctrl:
        show_all_branches = st.checkbox("Show all branches (including remote)", value=True)
    with col_refresh:
        if st.button("🔄 Refresh Branches", use_container_width=True):
            with st.spinner("Loading branches..."):
                result = _invoke(tools, "git_branches", {"path": repo_path, "all_branches": show_all_branches})
                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_git_branches_list"] = result.get("branches") or []
                    st.session_state["_git_current_branch"] = result.get("current") or "N/A"
                    st.success("Updated!")
                    st.rerun()

    # Branch Stats
    local_branches = [b for b in branches_list if not b.get("name", "").startswith("remotes/")]
    remote_branches = [b for b in branches_list if b.get("name", "").startswith("remotes/")]

    col_b1, col_b2, col_b3 = st.columns(3)
    with col_b1:
        st.metric("Local Branches", len(local_branches))
    with col_b2:
        st.metric("Remote Branches", len(remote_branches))
    with col_b3:
        st.metric("Current", current_branch[:15] + "..." if len(current_branch) > 15 else current_branch)

    st.divider()

    # Local Branches
    st.markdown("#### 📁 Local Branches")

    st.markdown('<div class="insight-card">', unsafe_allow_html=True)

    for branch in local_branches:
        name = branch.get("name", "")
        is_current = branch.get("current", False)

        col_name, col_status, col_action = st.columns([3, 1, 1])

        with col_name:
            if is_current:
                st.markdown(
                    f"""
                    <div class="branch-health">
                        <span class="health-dot health-healthy"></span>
                        <strong style="color: #22c55e;">{name}</strong>
                        <span style="background: #dcfce7; color: #16a34a; padding: 0.2rem 0.5rem; border-radius: 12px; font-size: 0.75rem;">current</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"""
                    <div class="branch-health">
                        <span class="health-dot health-healthy"></span>
                        <span>{name}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        with col_action:
            if not is_current:
                if st.button("Checkout", key=f"checkout_{name}", type="secondary"):
                    with st.spinner(f"Checking out {name}..."):
                        result = _invoke(tools, "git_checkout", {"ref": name, "path": repo_path})
                        if isinstance(result, dict) and result.get("ok"):
                            st.success(f"Checked out {name}")
                            st.session_state["_git_current_branch"] = name
                            st.rerun()
                        else:
                            st.error(f"Failed: {result}")

    st.markdown('</div>', unsafe_allow_html=True)

    # Remote Branches
    if remote_branches and show_all_branches:
        st.markdown("#### 🌐 Remote Branches")

        with st.expander(f"View {len(remote_branches)} remote branches", expanded=False):
            for branch in remote_branches[:50]:
                name = branch.get("name", "")
                st.markdown(f"- `{name}`")

            if len(remote_branches) > 50:
                st.caption(f"... and {len(remote_branches) - 50} more")


# ─────────────────────────────────────────────────────────────────────────────
# ISSUES & RECOMMENDATIONS TAB
# ─────────────────────────────────────────────────────────────────────────────

with tab_issues:
    st.markdown("### ⚠️ Issues & Recommendations")

    # Issues Section
    st.markdown("#### 🔍 Detected Issues")

    critical_issues = [i for i in issues if i.get("priority") == "high"]
    warning_issues = [i for i in issues if i.get("priority") == "medium"]
    info_issues = [i for i in issues if i.get("priority") == "low"]
    success_issues = [i for i in issues if i.get("priority") == "none"]

    # Show counts
    col_c1, col_c2, col_c3, col_c4 = st.columns(4)
    with col_c1:
        st.metric("Critical", len(critical_issues), delta=None)
    with col_c2:
        st.metric("Warnings", len(warning_issues), delta=None)
    with col_c3:
        st.metric("Info", len(info_issues), delta=None)
    with col_c4:
        st.metric("All Good", len(success_issues), delta=None)

    st.divider()

    # Display issues by priority
    for issue in critical_issues:
        st.markdown(
            f"""
            <div class="issue-card issue-critical">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <strong>🚨 {issue.get('title', '')}</strong>
                    <span class="priority-badge priority-high">High Priority</span>
                </div>
                <p style="margin: 0.5rem 0 0 0; color: #7f1d1d;">{issue.get('description', '')}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    for issue in warning_issues:
        st.markdown(
            f"""
            <div class="issue-card issue-warning">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <strong>⚠️ {issue.get('title', '')}</strong>
                    <span class="priority-badge priority-medium">Medium Priority</span>
                </div>
                <p style="margin: 0.5rem 0 0 0; color: #78350f;">{issue.get('description', '')}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    for issue in info_issues:
        st.markdown(
            f"""
            <div class="issue-card issue-info">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <strong>ℹ️ {issue.get('title', '')}</strong>
                    <span class="priority-badge priority-low">Low Priority</span>
                </div>
                <p style="margin: 0.5rem 0 0 0; color: #1e40af;">{issue.get('description', '')}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    for issue in success_issues:
        st.markdown(
            f"""
            <div class="issue-card issue-success">
                <strong>✅ {issue.get('title', '')}</strong>
                <p style="margin: 0.5rem 0 0 0; color: #166534;">{issue.get('description', '')}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()

    # Recommendations Section
    st.markdown("#### 💡 Recommendations")

    if recommendations:
        for idx, rec in enumerate(recommendations, 1):
            priority_class = f"priority-{rec.get('priority', 'low')}"
            st.markdown(
                f"""
                <div class="insight-card">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <div>
                            <h4 style="margin: 0 0 0.5rem 0; color: #1f2937;">💡 {rec.get('title', '')}</h4>
                            <p style="margin: 0; color: #4b5563;">{rec.get('description', '')}</p>
                        </div>
                        <span class="priority-badge {priority_class}">{rec.get('priority', 'low').title()}</span>
                    </div>
                    <div style="margin-top: 0.75rem;">
                        <span style="background: #f1f5f9; color: #475569; padding: 0.2rem 0.6rem; border-radius: 12px; font-size: 0.75rem;">
                            {rec.get('category', 'general').title()}
                        </span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.success("✨ No specific recommendations at this time. Keep up the good work!")


# ─────────────────────────────────────────────────────────────────────────────
# ACTIONS TAB
# ─────────────────────────────────────────────────────────────────────────────

with tab_actions:
    st.markdown("### ⚡ Git Actions")

    col_actions1, col_actions2 = st.columns(2)

    with col_actions1:
        st.markdown("#### 📥 Fetch & Pull")

        st.markdown('<div class="insight-card">', unsafe_allow_html=True)

        if st.button("📥 Fetch All Remotes", use_container_width=True, type="primary"):
            with st.spinner("Fetching..."):
                result = _invoke(tools, "git_fetch", {"path": repo_path, "prune": True})
                if isinstance(result, dict) and result.get("ok"):
                    st.success("Fetched from all remotes!")
                else:
                    st.error(f"Failed: {result}")

        st.divider()

        st.markdown("#### 📄 View Diff")

        staged_only = st.checkbox("Staged changes only", value=False)
        stat_only = st.checkbox("Show stat only", value=False)

        if st.button("📄 Get Diff", use_container_width=True):
            with st.spinner("Loading diff..."):
                result = _invoke(tools, "git_diff", {"path": repo_path, "staged": staged_only, "stat": stat_only})
                if isinstance(result, dict) and result.get("ok"):
                    diff_content = result.get("diff", "")
                    if diff_content:
                        st.code(diff_content[:10000], language="diff")
                    else:
                        st.info("No differences found")
                else:
                    st.error(f"Failed: {result}")

        st.markdown('</div>', unsafe_allow_html=True)

    with col_actions2:
        st.markdown("#### 🏷️ Tags")

        st.markdown('<div class="insight-card">', unsafe_allow_html=True)

        if st.button("🔄 Refresh Tags", use_container_width=True):
            with st.spinner("Loading tags..."):
                result = _invoke(tools, "git_tags", {"path": repo_path, "limit": 50})
                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_git_tags_list"] = result.get("tags") or []
                    st.success("Updated!")
                    st.rerun()

        if tags_list:
            st.markdown("**Recent Tags:**")
            for tag in tags_list[:10]:
                st.markdown(f"- `{tag}`")
            if len(tags_list) > 10:
                st.caption(f"... and {len(tags_list) - 10} more")
        else:
            st.info("No tags found")

        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("#### 📦 Clone Repository")

        st.markdown('<div class="insight-card">', unsafe_allow_html=True)

        clone_url = st.text_input("Repository URL", placeholder="https://github.com/user/repo.git")
        clone_dest = st.text_input("Destination path", placeholder="/repos/new-repo")

        if st.button("📦 Clone", use_container_width=True, type="primary"):
            if clone_url.strip() and clone_dest.strip():
                with st.spinner("Cloning..."):
                    result = _invoke(tools, "git_clone", {"url": clone_url.strip(), "dest": clone_dest.strip()})
                    if isinstance(result, dict) and result.get("ok"):
                        st.success(f"Cloned to {clone_dest}")
                    else:
                        st.error(f"Failed: {result}")
            else:
                st.warning("Please provide both URL and destination")

        st.markdown('</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TOOLS TAB
# ─────────────────────────────────────────────────────────────────────────────

with tab_tools:
    st.markdown("### 🔧 MCP Tools & Debugging")

    col_info, col_refresh = st.columns([3, 1])
    with col_info:
        st.markdown(f"**Connected to:** `{git_url}`")
        st.markdown(f"**Available Tools:** {len(tools)}")
    with col_refresh:
        if st.button("🔄 Reload Tools", use_container_width=True):
            tools = _get_git_tools(force_reload=True)
            st.success("Tools reloaded!")
            st.rerun()

    st.divider()

    # Health Check
    st.markdown("#### 🏥 Health Check")

    if st.button("Run Health Check", use_container_width=True, type="primary"):
        with st.spinner("Checking..."):
            result = _invoke(tools, "git_health_check", {})
            if isinstance(result, dict):
                if result.get("ok"):
                    st.success("Git is healthy and accessible")
                else:
                    st.error("Git reported issues")
                st.json(result)
            else:
                st.code(str(result))

    st.divider()

    # Tool List
    st.markdown("#### 📋 Available Tools")

    with st.expander("View All Tools", expanded=False):
        for idx, tool in enumerate(tools, 1):
            tool_name = tool.get("name", "unknown") if isinstance(tool, dict) else str(tool)
            tool_desc = tool.get("description", "") if isinstance(tool, dict) else ""
            st.markdown(f"**{idx}. `{tool_name}`**")
            if tool_desc:
                st.caption(tool_desc[:200])


# ─────────────────────────────────────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    f"**Repository:** `{repo_path}` • "
    f"**Branch:** `{current_branch}` • "
    f"**Health Score:** {health_score}% • "
    "Use the sidebar to configure settings and trigger actions."
)
