import streamlit as st
import pandas as pd
import pytz
import plotly.express as px
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta

# Your DB + ES utilities
from utils.elasticsearch import show_trigger_history
from utils.postgres import query_to_df

# ------------------------------------------------------------
# 🎨 Page Setup
# ------------------------------------------------------------
st.set_page_config(
    page_title="Streamlit Activity Monitor",
    layout="wide",
    page_icon="📊",
)

# ------------------------------------------------------------
# 🎨 Theme / Styling
# ------------------------------------------------------------
COLOR_SEQUENCE = [
    "#6366f1", "#8b5cf6", "#ec4899", "#f59e0b",
    "#10b981", "#06b6d4", "#3b82f6", "#ef4444",
    "#84cc16", "#a855f7",
]
PLOTLY_TEMPLATE = "plotly_white"
PLOTLY_LAYOUT = dict(
    margin=dict(l=20, r=20, t=50, b=20),
    font=dict(family="Inter, system-ui, sans-serif", size=12, color="#334155"),
    title_font=dict(size=15, color="#0f172a"),
    legend=dict(font=dict(size=11)),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
)

st.markdown("""
<style>
.block-container { padding-top: 2rem; padding-bottom: 3rem; }

/* metric cards */
.metric-card {
    padding: 18px 22px;
    border-radius: 14px;
    background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
    border: 1px solid #e2e8f0;
    box-shadow: 0 1px 3px rgba(15,23,42,0.04);
    position: relative;
    overflow: hidden;
    height: 100%;
}
.metric-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, #6366f1, #8b5cf6);
}
.metric-card.accent-green::before { background: linear-gradient(90deg, #10b981, #059669); }
.metric-card.accent-amber::before { background: linear-gradient(90deg, #f59e0b, #d97706); }
.metric-card.accent-pink::before  { background: linear-gradient(90deg, #ec4899, #db2777); }
.metric-label {
    font-size: 0.78rem;
    color: #64748b;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
.metric-value {
    font-size: 2.1rem;
    font-weight: 700;
    color: #0f172a;
    line-height: 1.1;
    margin-top: 4px;
}
.metric-sub {
    font-size: 0.8rem;
    color: #64748b;
    margin-top: 4px;
}

/* live dot */
.live-dot {
    display: inline-block;
    width: 9px; height: 9px;
    border-radius: 50%;
    background: #10b981;
    box-shadow: 0 0 0 0 rgba(16,185,129,0.7);
    animation: pulse 2s infinite;
    margin-right: 6px;
    vertical-align: middle;
}
@keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(16,185,129,0.6); }
    70%  { box-shadow: 0 0 0 10px rgba(16,185,129,0); }
    100% { box-shadow: 0 0 0 0 rgba(16,185,129,0); }
}

/* top entry cards */
.top-card {
    padding: 12px 14px;
    border-radius: 10px;
    background: #fafbff;
    border: 1px solid #e0e7ff;
    border-left: 4px solid #6366f1;
    margin-bottom: 8px;
    transition: transform 0.15s ease;
}
.top-card:hover { transform: translateX(2px); }
.top-card b { color: #1e293b; }

/* chip */
.chip {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    background: #eef2ff;
    color: #4338ca;
    font-size: 0.78rem;
    font-weight: 500;
    margin-right: 4px;
}
.chip-amber { background: #fef3c7; color: #92400e; }

/* live status bar */
.status-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 18px;
    padding: 10px 16px;
    background: linear-gradient(90deg, #f0fdf4 0%, #ecfdf5 100%);
    border: 1px solid #a7f3d0;
    border-radius: 10px;
    font-size: 0.92rem;
    color: #065f46;
    margin: 12px 0 18px;
}

.section-divider {
    height: 1px;
    background: linear-gradient(90deg, transparent, #cbd5e1, transparent);
    margin: 24px 0 18px;
    border: none;
}
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------
# 🔧 Settings
# ------------------------------------------------------------
DEVOPS_USERNAMES = [
    "marwan_bakeer", "adham_wagih", "karam_mohamed",
    "ahmed_elhanafy", "ahmed_abdelkhalik", "hesham_mostafa", "salma_adel",
]

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def load_session_data(start_time, end_time):
    query = """
        SELECT *
        FROM session_states
        WHERE timestamp >= :start_ts
          AND timestamp <= :end_ts
    """
    params = {"start_ts": start_time.isoformat(), "end_ts": end_time.isoformat()}
    df = query_to_df(query, params)
    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Africa/Cairo")
    df["username"] = df["username"].astype(str).str.lower()

    df["original_user"] = df.get("original_user", None)
    df["current_page"] = df.get("current_page", None)
    df["session_id"] = df.get("session_id", pd.util.hash_pandas_object(df["timestamp"], index=False))

    df["is_assumed"] = (
        df["original_user"].notna()
        & (df["original_user"] != df["username"])
        & (df["original_user"] != "None")
    )

    df["display_user"] = df.apply(
        lambda r: f"{r['username']} 👤 (assumed by {r['original_user']})"
        if r["is_assumed"] else r["username"],
        axis=1,
    )
    return df


def humanize_time_diff(past_time, now=None):
    now = now or datetime.now(timezone.utc)
    if past_time.tzinfo is None:
        past_time = past_time.replace(tzinfo=timezone.utc)
    else:
        past_time = past_time.astimezone(timezone.utc)
    diff = relativedelta(now, past_time)
    if diff.years:   return f"{diff.years}y ago"
    if diff.months:  return f"{diff.months}mo ago"
    if diff.days:    return f"{diff.days}d ago"
    if diff.hours:   return f"{diff.hours}h ago"
    if diff.minutes: return f"{diff.minutes}m ago"
    return "just now"


def style_fig(fig):
    fig.update_layout(**PLOTLY_LAYOUT)
    fig.update_xaxes(showgrid=True, gridcolor="#f1f5f9", zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f1f5f9", zeroline=False)
    return fig


def is_devops_display_user(display_user: str) -> bool:
    return any(display_user.startswith(d) for d in DEVOPS_USERNAMES)


# ------------------------------------------------------------
# 🏷️ Header
# ------------------------------------------------------------
st.markdown("""
<div style="margin-bottom: 8px;">
    <h1 style="margin: 0; font-weight: 700; color: #0f172a;">📊 Streamlit Activity Monitor</h1>
    <p style="margin: 0; color: #64748b; font-size: 0.95rem;">
        Real-time visibility into platform usage, user journeys, and engagement patterns.
    </p>
</div>
""", unsafe_allow_html=True)

# ============================================================
# 🚀 Jenkins History
# ============================================================
with st.expander("🚀 Jenkins Pipeline Trigger History (Elasticsearch)"):
    show_trigger_history()

# ============================================================
# 🕒 Time Window
# ============================================================
st.markdown('<hr class="section-divider"/>', unsafe_allow_html=True)
st.markdown("### 🔍 Time Window")

max_lookback_months = 6
max_lookback_days = max_lookback_months * 31

c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    lookback_num = st.number_input(
        "Look back",
        min_value=1, max_value=max_lookback_days, value=7, step=1,
    )
with c2:
    lookback_unit = st.selectbox("Units", options=["hours", "days", "months"], index=1)
with c3:
    show_devops = st.toggle(
        "Show DevOps Members", value=False,
        help="Toggle to include/exclude DevOps usernames from all analytics",
    )

now_utc = datetime.now(pytz.UTC)
if lookback_unit == "hours":
    start_time = now_utc - timedelta(hours=lookback_num)
elif lookback_unit == "days":
    start_time = now_utc - timedelta(days=lookback_num)
else:
    start_time = now_utc - pd.DateOffset(months=lookback_num)

end_time = now_utc
if start_time < now_utc - pd.DateOffset(months=max_lookback_months):
    start_time = now_utc - pd.DateOffset(months=max_lookback_months)

window_start_local = pd.Timestamp(start_time).tz_convert("Africa/Cairo")
window_end_local = pd.Timestamp(end_time).tz_convert("Africa/Cairo")
st.caption(
    f"📅 Showing **{lookback_num} {lookback_unit}** • "
    f"{window_start_local.strftime('%Y-%m-%d %H:%M')} → "
    f"{window_end_local.strftime('%Y-%m-%d %H:%M')} (Africa/Cairo)"
)

# ============================================================
# 📥 Load
# ============================================================
df = load_session_data(start_time, end_time)
if df.empty:
    st.warning("⚠ No session data available for the selected time range.")
    st.stop()

if not show_devops:
    df = df[~df["username"].isin(DEVOPS_USERNAMES)]

# ============================================================
# 🎛 Filters
# ============================================================
st.markdown("### 🎛 Filters")
fc1, fc2, fc3 = st.columns([1, 1, 2])
with fc1:
    include_assumed = st.toggle("Include assumed interactions", value=False)

if not include_assumed:
    df = df[~df["is_assumed"]]

with fc2:
    selected_user = st.selectbox("User", ["All"] + sorted(df["display_user"].unique()))
    if selected_user != "All":
        df = df[df["display_user"] == selected_user]

with fc3:
    selected_page = st.selectbox("Page", ["All"] + sorted(df["current_page"].dropna().unique()))
    if selected_page != "All":
        df = df[df["current_page"] == selected_page]

if df.empty:
    st.warning("⚠ No data matches the current filters.")
    st.stop()

# ------------------------------------------------------------
# 📊 Active sessions (live)
# ------------------------------------------------------------
current_time_utc = datetime.now(pytz.UTC)
active_window = current_time_utc - timedelta(minutes=15)
active_sessions_df = df[df["timestamp"] >= active_window]
active_users = sorted(active_sessions_df["display_user"].unique())

last_event = df["timestamp"].max()
last_event_str = humanize_time_diff(last_event.to_pydatetime())
st.markdown(
    f"""<div class="status-bar">
        <span><span class="live-dot"></span><b>{len(active_users)}</b> active in last 15 min</span>
        <span>📡 Last interaction: <b>{last_event_str}</b></span>
        <span>📊 <b>{len(df):,}</b> total interactions in window</span>
    </div>""",
    unsafe_allow_html=True,
)

# ------------------------------------------------------------
# Time-derived columns (DST-safe: drop tz before flooring)
# ------------------------------------------------------------
df_time = df.copy()
ts_naive = df_time["timestamp"].dt.tz_localize(None)
df_time["day"] = ts_naive.dt.floor("D")
df_time["hour"] = ts_naive.dt.hour
df_time["dow"] = ts_naive.dt.day_name()

# ============================================================
# 📈 TABS
# ============================================================
tab1, tab2, tab3 = st.tabs([
    "📈 Overview",
    "🔥 Activity Patterns",
    "🧭 User Journey",
])

# ------------------------------------------------------------
# 📈 TAB 1 — OVERVIEW
# ------------------------------------------------------------
with tab1:
    st.markdown("#### 📦 Summary")

    n_sessions = df["session_id"].nunique()
    n_interactions = len(df)
    n_users = df["username"].nunique()
    avg_per_user = n_interactions / max(n_users, 1)

    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">🗂 Sessions</div>
            <div class="metric-value">{n_sessions:,}</div>
            <div class="metric-sub">distinct sessions</div>
        </div>""", unsafe_allow_html=True)
    with mc2:
        st.markdown(f"""
        <div class="metric-card accent-pink">
            <div class="metric-label">🔁 Interactions</div>
            <div class="metric-value">{n_interactions:,}</div>
            <div class="metric-sub">{avg_per_user:.1f} per user avg</div>
        </div>""", unsafe_allow_html=True)
    with mc3:
        st.markdown(f"""
        <div class="metric-card accent-amber">
            <div class="metric-label">👥 Unique Users</div>
            <div class="metric-value">{n_users:,}</div>
            <div class="metric-sub">in selected window</div>
        </div>""", unsafe_allow_html=True)
    with mc4:
        st.markdown(f"""
        <div class="metric-card accent-green">
            <div class="metric-label">🟢 Active Now</div>
            <div class="metric-value">{len(active_users)}</div>
            <div class="metric-sub">last 15 min</div>
        </div>""", unsafe_allow_html=True)

    # ⭐ Top stats
    st.markdown("#### ⭐ Top Stats")
    tc1, tc2 = st.columns(2)

    with tc1:
        st.markdown("**📚 Top Pages**")
        page_stats = (
            df.groupby("current_page")
            .agg(
                visits=("current_page", "count"),
                unique_users=("username", "nunique"),
            )
            .sort_values("visits", ascending=False)
            .head(5)
        )
        max_visits = page_stats["visits"].max() if len(page_stats) else 1
        for page, row in page_stats.iterrows():
            pct = row["visits"] / max_visits * 100
            st.markdown(f"""
            <div class="top-card">
                <b>{page}</b><br>
                <span class="chip">🔁 {row['visits']} visits</span>
                <span class="chip">👥 {row['unique_users']} users</span>
                <div style="height: 4px; border-radius: 4px; background: #e2e8f0; margin-top: 8px;">
                    <div style="height: 100%; width: {pct:.0f}%; border-radius: 4px;
                                background: linear-gradient(90deg, #6366f1, #8b5cf6);"></div>
                </div>
            </div>""", unsafe_allow_html=True)

    with tc2:
        st.markdown("**🏆 Most Active Users**")
        top_users = df["display_user"].value_counts().head(5)
        max_count = top_users.max() if len(top_users) else 1
        for user, count in top_users.items():
            pct = count / max_count * 100
            badge = '<span class="chip chip-amber">DevOps</span>' if is_devops_display_user(user) else ""
            st.markdown(f"""
            <div class="top-card">
                <b>{user}</b> {badge}
                <span class="chip">{count} interactions</span>
                <div style="height: 4px; border-radius: 4px; background: #e2e8f0; margin-top: 8px;">
                    <div style="height: 100%; width: {pct:.0f}%; border-radius: 4px;
                                background: linear-gradient(90deg, #ec4899, #f59e0b);"></div>
                </div>
            </div>""", unsafe_allow_html=True)

    # 📉 Trends
    st.markdown("#### 📊 Interaction Trends")
    pages_time = df_time.groupby(["day", "current_page"]).size().reset_index(name="interactions")
    users_time = df_time.groupby(["day", "display_user"]).size().reset_index(name="interactions")

    cpg, cus = st.columns(2)
    with cpg:
        fig_pages = px.area(
            pages_time, x="day", y="interactions", color="current_page",
            title="📚 Page Interactions Over Time",
            template=PLOTLY_TEMPLATE,
            color_discrete_sequence=COLOR_SEQUENCE,
        )
        st.plotly_chart(style_fig(fig_pages), width="stretch")
    with cus:
        fig_users = px.area(
            users_time, x="day", y="interactions", color="display_user",
            title="👤 User Interactions Over Time",
            template=PLOTLY_TEMPLATE,
            color_discrete_sequence=COLOR_SEQUENCE,
        )
        st.plotly_chart(style_fig(fig_users), width="stretch")

    # User activity summary (kept from original)
    st.markdown("#### 📈 User Activity Summary")
    total_users = users_time["display_user"].nunique()
    total_interactions = users_time["interactions"].sum()
    avg_daily_users = users_time.groupby("day")["display_user"].nunique().mean()
    s1, s2, s3 = st.columns(3)
    with s1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">Total Unique Users</div>
            <div class="metric-value">{total_users:,}</div>
        </div>""", unsafe_allow_html=True)
    with s2:
        st.markdown(f"""
        <div class="metric-card accent-pink">
            <div class="metric-label">Total Interactions</div>
            <div class="metric-value">{total_interactions:,}</div>
        </div>""", unsafe_allow_html=True)
    with s3:
        st.markdown(f"""
        <div class="metric-card accent-amber">
            <div class="metric-label">Avg Daily Users</div>
            <div class="metric-value">{avg_daily_users:.1f}</div>
        </div>""", unsafe_allow_html=True)

    # Daily users
    st.markdown("#### 👥 Daily Active Users")
    daily_user_counts = df_time.groupby("day")["display_user"].nunique().reset_index(name="unique_users")
    fig_daily = px.line(
        daily_user_counts, x="day", y="unique_users",
        template=PLOTLY_TEMPLATE,
        markers=True,
    )
    fig_daily.update_traces(line=dict(color="#6366f1", width=3), marker=dict(color="#6366f1", size=8))
    fig_daily.update_layout(height=300, xaxis_title="Date", yaxis_title="Unique Users")
    st.plotly_chart(style_fig(fig_daily), width="stretch")


# ------------------------------------------------------------
# 🔥 TAB 2 — ACTIVITY PATTERNS
# ------------------------------------------------------------
with tab2:
    st.markdown("#### 🔥 When are users active?")
    st.caption("Day-of-week × hour-of-day heatmap. Helps you plan deploys, maintenance, and outreach.")

    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    heat = df_time.groupby(["dow", "hour"]).size().reset_index(name="interactions")
    heat_pivot = (
        heat.pivot(index="dow", columns="hour", values="interactions")
        .reindex(day_order)
        .reindex(columns=range(24))
        .fillna(0)
    )
    fig_heat = px.imshow(
        heat_pivot,
        labels=dict(x="Hour of day (Cairo)", y="Day of week", color="Interactions"),
        x=[f"{h:02d}" for h in range(24)],
        y=day_order,
        color_continuous_scale=["#f8fafc", "#c7d2fe", "#818cf8", "#6366f1", "#4338ca"],
        aspect="auto",
    )
    fig_heat.update_layout(height=320)
    st.plotly_chart(style_fig(fig_heat), width="stretch")

    # Engagement
    st.markdown("#### 📈 Engagement")
    interactions_per_session = df.groupby("session_id").size()
    avg_session_size = interactions_per_session.mean()
    median_session_size = interactions_per_session.median()
    sessions_per_day = df_time.groupby("day")["session_id"].nunique().mean()
    pages_per_user = df.groupby("username")["current_page"].nunique().mean()

    e1, e2, e3, e4 = st.columns(4)
    with e1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">🔁 Avg / Session</div>
            <div class="metric-value">{avg_session_size:.1f}</div>
            <div class="metric-sub">interactions per session</div>
        </div>""", unsafe_allow_html=True)
    with e2:
        st.markdown(f"""
        <div class="metric-card accent-pink">
            <div class="metric-label">📊 Median / Session</div>
            <div class="metric-value">{median_session_size:.0f}</div>
            <div class="metric-sub">interactions per session</div>
        </div>""", unsafe_allow_html=True)
    with e3:
        st.markdown(f"""
        <div class="metric-card accent-amber">
            <div class="metric-label">📅 Sessions / Day</div>
            <div class="metric-value">{sessions_per_day:.1f}</div>
            <div class="metric-sub">avg per day in window</div>
        </div>""", unsafe_allow_html=True)
    with e4:
        st.markdown(f"""
        <div class="metric-card accent-green">
            <div class="metric-label">📚 Pages / User</div>
            <div class="metric-value">{pages_per_user:.1f}</div>
            <div class="metric-sub">unique pages per user</div>
        </div>""", unsafe_allow_html=True)

    # Page transitions
    st.markdown("#### 🔀 Most Common Page Transitions")
    st.caption("Top consecutive page → page moves within the same session.")

    transitions_df = (
        df.sort_values(["session_id", "timestamp"])
        .assign(next_page=lambda d: d.groupby("session_id")["current_page"].shift(-1))
        .dropna(subset=["current_page", "next_page"])
    )
    transitions_df = transitions_df[transitions_df["current_page"] != transitions_df["next_page"]]

    if len(transitions_df) > 0:
        top_transitions = (
            transitions_df.groupby(["current_page", "next_page"])
            .size()
            .reset_index(name="count")
            .sort_values("count", ascending=False)
            .head(10)
        )
        top_transitions["transition"] = top_transitions["current_page"] + "  →  " + top_transitions["next_page"]
        fig_trans = px.bar(
            top_transitions[::-1],
            x="count", y="transition",
            orientation="h",
            template=PLOTLY_TEMPLATE,
            color="count",
            color_continuous_scale=["#c7d2fe", "#6366f1", "#4338ca"],
        )
        fig_trans.update_layout(
            height=400, yaxis_title=None, xaxis_title="Count", coloraxis_showscale=False,
        )
        st.plotly_chart(style_fig(fig_trans), width="stretch")
    else:
        st.info("Not enough multi-page sessions to compute transitions.")


# ------------------------------------------------------------
# 🧭 TAB 3 — USER JOURNEY
# ------------------------------------------------------------
with tab3:
    st.markdown("#### 🗺 User Activity Timelines")
    st.caption("Sorted by most-recent activity. 🟢 = active in the last 15 minutes.")

    df_sorted = df.sort_values("timestamp")
    user_groups = df_sorted.groupby("display_user")
    now = datetime.now(timezone.utc)

    for display_user, user_df in sorted(user_groups, key=lambda g: g[1]["timestamp"].max(), reverse=True):
        last_activity = user_df["timestamp"].max()
        user_fav_page = user_df["current_page"].mode().iloc[0] if user_df["current_page"].notna().any() else "N/A"
        live_marker = "🟢 " if display_user in active_users else ""

        with st.expander(
            f"{live_marker}{display_user} — {len(user_df)} interactions — Favorite: {user_fav_page} — "
            f"Last seen: {humanize_time_diff(last_activity.to_pydatetime(), now)}"
        ):
            u = user_df.sort_values("timestamp", ascending=True).copy()
            order = {p: i for i, p in enumerate(u["current_page"].dropna().unique())}
            u["page_idx"] = u["current_page"].map(order)

            fig = px.line(
                u, x="timestamp", y="page_idx", color="current_page",
                markers=True,
                title="Navigation Timeline",
                hover_data={"session_id": True},
                color_discrete_sequence=COLOR_SEQUENCE,
                template=PLOTLY_TEMPLATE,
            )
            fig.update_yaxes(tickvals=list(order.values()), ticktext=list(order.keys()))
            fig.update_layout(height=320)
            st.plotly_chart(style_fig(fig), width="stretch")

            st.dataframe(u[["timestamp", "current_page", "session_id"]], width="stretch")
