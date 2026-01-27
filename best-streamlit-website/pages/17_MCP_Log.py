"""MCP Log - Insights and analytics for MCP tool interactions."""

from datetime import datetime, timedelta
from typing import Any, Dict, List

import streamlit as st

from src.admin_config import load_admin_config
from src.theme import set_theme
from src.mcp_health import add_mcp_status_styles


set_theme(page_title="MCP Log", page_icon="📊")

admin = load_admin_config()

# Add status badge styles
add_mcp_status_styles()

# Custom styling
st.markdown(
    """
    <style>
    .mcp-log-hero {
        background: linear-gradient(135deg, #0ea5e9 0%, #06b6d4 50%, #14b8a6 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(14, 165, 233, 0.3);
    }
    .mcp-log-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        letter-spacing: 0.5px;
    }
    .mcp-log-hero p {
        margin: 0;
        font-size: 1.05rem;
        opacity: 0.95;
    }
    .stat-card {
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
        text-align: center;
    }
    .stat-value {
        font-size: 2.5rem;
        font-weight: 800;
        color: #0f172a;
        line-height: 1;
    }
    .stat-label {
        font-size: 0.9rem;
        color: #64748b;
        margin-top: 0.5rem;
    }
    .stat-change {
        font-size: 0.8rem;
        margin-top: 0.25rem;
    }
    .stat-change-positive {
        color: #22c55e;
    }
    .stat-change-negative {
        color: #ef4444;
    }
    .log-card {
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
        margin-bottom: 1rem;
    }
    .log-card h3 {
        font-size: 1.2rem;
        font-weight: 700;
        margin: 0 0 1rem 0;
        color: #1e293b;
    }
    .server-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.85rem;
        font-weight: 500;
        margin: 0.25rem;
    }
    .server-badge-kubernetes { background: #dbeafe; color: #1d4ed8; }
    .server-badge-docker { background: #e0e7ff; color: #4338ca; }
    .server-badge-jenkins { background: #fef3c7; color: #b45309; }
    .server-badge-nexus { background: #d1fae5; color: #047857; }
    .server-badge-git { background: #fce7f3; color: #be185d; }
    .server-badge-trivy { background: #ede9fe; color: #6d28d9; }
    .status-success {
        color: #22c55e;
        font-weight: 600;
    }
    .status-error {
        color: #ef4444;
        font-weight: 600;
    }
    .log-entry {
        background: #f8fafc;
        border-radius: 8px;
        padding: 0.75rem 1rem;
        margin: 0.5rem 0;
        border-left: 3px solid #e2e8f0;
    }
    .log-entry-success {
        border-left-color: #22c55e;
    }
    .log-entry-error {
        border-left-color: #ef4444;
    }
    .chart-container {
        background: #ffffff;
        border-radius: 12px;
        padding: 1rem;
        border: 1px solid #e2e8f0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="mcp-log-hero">
        <h1>MCP Log & Analytics</h1>
        <p>Monitor tool usage, performance metrics, and error trends across all MCP servers</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Initialize database and load functions
try:
    from src.mcp_log import (
        init_db,
        get_tool_calls,
        get_tool_call_stats,
        get_server_stats,
        get_hourly_stats,
        get_recent_errors,
        cleanup_old_logs,
    )
    from src.mcp_log.repo import get_tool_stats

    # Initialize database
    init_db()
    db_available = True
except Exception as e:
    db_available = False
    db_error = str(e)

if not db_available:
    st.error(f"Failed to connect to MCP log database: {db_error}")
    st.info("Make sure the database is configured correctly in your environment.")
    st.stop()

# Sidebar controls
with st.sidebar:
    st.markdown("### Time Range")

    time_options = {
        "Last Hour": timedelta(hours=1),
        "Last 6 Hours": timedelta(hours=6),
        "Last 24 Hours": timedelta(hours=24),
        "Last 7 Days": timedelta(days=7),
        "Last 30 Days": timedelta(days=30),
    }

    selected_range = st.selectbox(
        "Select time range",
        options=list(time_options.keys()),
        index=2,  # Default to Last 24 Hours
    )

    time_delta = time_options[selected_range]
    since = datetime.utcnow() - time_delta
    until = datetime.utcnow()

    st.divider()

    st.markdown("### Filters")

    # Get server list from stats
    all_server_stats = get_server_stats(since=since, until=until)
    server_list = ["All Servers"] + [s["server_name"] for s in all_server_stats]

    selected_server = st.selectbox(
        "Server",
        options=server_list,
        index=0,
    )

    status_filter = st.selectbox(
        "Status",
        options=["All", "Success", "Failed"],
        index=0,
    )

    st.divider()

    if st.button("Refresh Data", use_container_width=True):
        st.rerun()

    st.divider()

    st.markdown("### Maintenance")

    if st.button("Cleanup Old Logs", use_container_width=True, type="secondary"):
        with st.spinner("Cleaning up..."):
            deleted = cleanup_old_logs()
            st.success(f"Deleted {deleted} old log entries")

# Main content
# Overview stats
stats = get_tool_call_stats(since=since, until=until)

st.markdown("### Overview")

stat_cols = st.columns(5)

with stat_cols[0]:
    st.markdown(
        f"""
        <div class="stat-card">
            <div class="stat-value">{stats['total_calls']:,}</div>
            <div class="stat-label">Total Calls</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with stat_cols[1]:
    success_color = "#22c55e" if stats['success_rate'] >= 90 else "#f59e0b" if stats['success_rate'] >= 70 else "#ef4444"
    st.markdown(
        f"""
        <div class="stat-card">
            <div class="stat-value" style="color: {success_color};">{stats['success_rate']}%</div>
            <div class="stat-label">Success Rate</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with stat_cols[2]:
    avg_duration = stats['avg_duration_ms'] or 0
    st.markdown(
        f"""
        <div class="stat-card">
            <div class="stat-value">{avg_duration:.0f}<span style="font-size: 1rem;">ms</span></div>
            <div class="stat-label">Avg Duration</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with stat_cols[3]:
    st.markdown(
        f"""
        <div class="stat-card">
            <div class="stat-value">{stats['unique_servers']}</div>
            <div class="stat-label">Active Servers</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with stat_cols[4]:
    st.markdown(
        f"""
        <div class="stat-card">
            <div class="stat-value" style="color: #ef4444;">{stats['failed_calls']:,}</div>
            <div class="stat-label">Failed Calls</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.divider()

# Tabs for different views
tabs = st.tabs(["Activity", "Servers", "Tools", "Errors", "Log Browser"])

# --- ACTIVITY TAB ---
with tabs[0]:
    st.markdown('<div class="log-card">', unsafe_allow_html=True)
    st.markdown("### Activity Over Time")

    hourly_stats = get_hourly_stats(since=since, until=until)

    if hourly_stats:
        # Create chart data
        import pandas as pd

        df = pd.DataFrame(hourly_stats)
        if not df.empty:
            df["hour"] = pd.to_datetime(df["hour"])
            df = df.set_index("hour")

            # Area chart for calls over time
            st.area_chart(
                df[["total_calls", "successful_calls", "failed_calls"]],
                color=["#0ea5e9", "#22c55e", "#ef4444"],
            )
    else:
        st.info("No activity data available for the selected time range.")

    st.markdown('</div>', unsafe_allow_html=True)

    # Recent activity summary
    col_recent1, col_recent2 = st.columns(2)

    with col_recent1:
        st.markdown('<div class="log-card">', unsafe_allow_html=True)
        st.markdown("### Calls by Server")

        server_stats = get_server_stats(since=since, until=until)
        if server_stats:
            for s in server_stats[:6]:
                server_name = s["server_name"]
                badge_class = f"server-badge-{server_name.lower()}" if server_name.lower() in ["kubernetes", "docker", "jenkins", "nexus", "git", "trivy"] else ""

                col_name, col_calls, col_rate = st.columns([2, 1, 1])
                with col_name:
                    st.markdown(f"<span class='server-badge {badge_class}'>{server_name}</span>", unsafe_allow_html=True)
                with col_calls:
                    st.markdown(f"**{s['total_calls']:,}** calls")
                with col_rate:
                    rate_color = "status-success" if s['success_rate'] >= 90 else "status-error"
                    st.markdown(f"<span class='{rate_color}'>{s['success_rate']}%</span>", unsafe_allow_html=True)
        else:
            st.info("No server data available.")

        st.markdown('</div>', unsafe_allow_html=True)

    with col_recent2:
        st.markdown('<div class="log-card">', unsafe_allow_html=True)
        st.markdown("### Performance Distribution")

        # Get duration distribution
        all_calls = get_tool_calls(since=since, until=until, limit=500)
        if all_calls:
            durations = [c["duration_ms"] for c in all_calls if c.get("duration_ms")]
            if durations:
                import pandas as pd

                df_dur = pd.DataFrame({"duration_ms": durations})

                # Show distribution
                fast = sum(1 for d in durations if d < 100)
                medium = sum(1 for d in durations if 100 <= d < 1000)
                slow = sum(1 for d in durations if d >= 1000)

                st.markdown(f"**Fast (<100ms):** {fast} ({fast/len(durations)*100:.1f}%)")
                st.progress(fast / len(durations) if durations else 0)

                st.markdown(f"**Medium (100ms-1s):** {medium} ({medium/len(durations)*100:.1f}%)")
                st.progress(medium / len(durations) if durations else 0)

                st.markdown(f"**Slow (>1s):** {slow} ({slow/len(durations)*100:.1f}%)")
                st.progress(slow / len(durations) if durations else 0)
            else:
                st.info("No duration data available.")
        else:
            st.info("No calls found in this time range.")

        st.markdown('</div>', unsafe_allow_html=True)

# --- SERVERS TAB ---
with tabs[1]:
    st.markdown('<div class="log-card">', unsafe_allow_html=True)
    st.markdown("### Server Performance")

    server_stats = get_server_stats(since=since, until=until)

    if server_stats:
        import pandas as pd

        df_servers = pd.DataFrame(server_stats)

        # Display as table with custom formatting
        st.dataframe(
            df_servers,
            use_container_width=True,
            hide_index=True,
            column_config={
                "server_name": st.column_config.TextColumn("Server", width="medium"),
                "total_calls": st.column_config.NumberColumn("Total Calls", format="%d"),
                "successful_calls": st.column_config.NumberColumn("Success", format="%d"),
                "failed_calls": st.column_config.NumberColumn("Failed", format="%d"),
                "success_rate": st.column_config.ProgressColumn("Success Rate", min_value=0, max_value=100, format="%.1f%%"),
                "avg_duration_ms": st.column_config.NumberColumn("Avg Duration (ms)", format="%.1f"),
                "max_duration_ms": st.column_config.NumberColumn("Max Duration (ms)", format="%.1f"),
                "unique_tools": st.column_config.NumberColumn("Tools Used", format="%d"),
            }
        )

        # Bar chart comparison
        st.markdown("#### Calls by Server")
        chart_data = df_servers.set_index("server_name")[["successful_calls", "failed_calls"]]
        st.bar_chart(chart_data, color=["#22c55e", "#ef4444"])
    else:
        st.info("No server data available for the selected time range.")

    st.markdown('</div>', unsafe_allow_html=True)

# --- TOOLS TAB ---
with tabs[2]:
    st.markdown('<div class="log-card">', unsafe_allow_html=True)
    st.markdown("### Most Used Tools")

    filter_server = None if selected_server == "All Servers" else selected_server
    tool_stats = get_tool_stats(server_name=filter_server, since=since, until=until, limit=25)

    if tool_stats:
        import pandas as pd

        df_tools = pd.DataFrame(tool_stats)

        st.dataframe(
            df_tools,
            use_container_width=True,
            hide_index=True,
            column_config={
                "server_name": st.column_config.TextColumn("Server", width="small"),
                "tool_name": st.column_config.TextColumn("Tool", width="large"),
                "total_calls": st.column_config.NumberColumn("Calls", format="%d"),
                "successful_calls": st.column_config.NumberColumn("Success", format="%d"),
                "success_rate": st.column_config.ProgressColumn("Success Rate", min_value=0, max_value=100, format="%.1f%%"),
                "avg_duration_ms": st.column_config.NumberColumn("Avg Duration (ms)", format="%.1f"),
            }
        )
    else:
        st.info("No tool data available for the selected time range.")

    st.markdown('</div>', unsafe_allow_html=True)

# --- ERRORS TAB ---
with tabs[3]:
    st.markdown('<div class="log-card">', unsafe_allow_html=True)
    st.markdown("### Recent Errors")

    errors = get_recent_errors(limit=30, since=since)

    if errors:
        for error in errors:
            status_class = "log-entry-error"
            st.markdown(
                f"""
                <div class="log-entry {status_class}">
                    <strong>{error['server_name']}</strong> / <code>{error['tool_name']}</code>
                    <br>
                    <span style="color: #64748b; font-size: 0.85rem;">
                        {error['started_at'][:19] if error.get('started_at') else 'Unknown time'}
                    </span>
                    <br>
                    <span style="color: #ef4444;">{error.get('error_type', 'Error')}: {error.get('error_message', 'Unknown error')[:200]}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # Error type breakdown
        st.markdown("#### Error Types")
        error_types = {}
        for e in errors:
            et = e.get("error_type", "Unknown")
            error_types[et] = error_types.get(et, 0) + 1

        for et, count in sorted(error_types.items(), key=lambda x: -x[1]):
            st.markdown(f"- **{et}**: {count} occurrences")
    else:
        st.success("No errors in the selected time range!")

    st.markdown('</div>', unsafe_allow_html=True)

# --- LOG BROWSER TAB ---
with tabs[4]:
    st.markdown('<div class="log-card">', unsafe_allow_html=True)
    st.markdown("### Log Browser")

    # Additional filters for log browser
    col_filter1, col_filter2, col_filter3 = st.columns(3)

    with col_filter1:
        browser_server = st.selectbox(
            "Filter by server",
            options=server_list,
            index=0,
            key="browser_server",
        )

    with col_filter2:
        browser_status = st.selectbox(
            "Filter by status",
            options=["All", "Success", "Failed"],
            index=0,
            key="browser_status",
        )

    with col_filter3:
        browser_limit = st.selectbox(
            "Results per page",
            options=[25, 50, 100, 200],
            index=1,
            key="browser_limit",
        )

    # Build query parameters
    query_params = {
        "since": since,
        "until": until,
        "limit": browser_limit,
    }

    if browser_server != "All Servers":
        query_params["server_name"] = browser_server

    if browser_status == "Success":
        query_params["success"] = True
    elif browser_status == "Failed":
        query_params["success"] = False

    # Fetch logs
    logs = get_tool_calls(**query_params)

    if logs:
        st.markdown(f"**Showing {len(logs)} log entries**")

        for log in logs:
            status_class = "log-entry-success" if log.get("success") else "log-entry-error"
            status_icon = "✅" if log.get("success") else "❌"
            duration = f"{log.get('duration_ms', 0):.0f}ms" if log.get("duration_ms") else "N/A"

            with st.expander(
                f"{status_icon} {log['server_name']} / {log['tool_name']} - {duration}",
                expanded=False
            ):
                col_info1, col_info2 = st.columns(2)

                with col_info1:
                    st.markdown(f"**ID:** `{log['id']}`")
                    st.markdown(f"**Server:** {log['server_name']}")
                    st.markdown(f"**Tool:** `{log['tool_name']}`")
                    st.markdown(f"**Status:** {'Success' if log['success'] else 'Failed'}")

                with col_info2:
                    st.markdown(f"**Started:** {log.get('started_at', 'N/A')}")
                    st.markdown(f"**Duration:** {duration}")
                    st.markdown(f"**Source:** {log.get('source', 'N/A')}")
                    st.markdown(f"**Session:** {log.get('session_id', 'N/A')}")

                if log.get("args_json"):
                    st.markdown("**Arguments:**")
                    try:
                        import json
                        args = json.loads(log["args_json"])
                        st.json(args)
                    except:
                        st.code(log["args_json"])

                if log.get("result_preview"):
                    st.markdown("**Result Preview:**")
                    st.code(log["result_preview"][:1000])

                if log.get("error_message"):
                    st.markdown("**Error:**")
                    st.error(f"{log.get('error_type', 'Error')}: {log['error_message']}")
    else:
        st.info("No logs found for the selected filters.")

    st.markdown('</div>', unsafe_allow_html=True)

# Footer
st.divider()
st.caption(
    "**Note:** MCP logs are automatically collected for tool calls made via the unified MCP client "
    "and the logging interceptor (for MultiServerMCPClient). "
    "Old logs are cleaned up based on the retention period (default: 30 days). "
    "All sensitive data (passwords, tokens) is automatically redacted."
)
