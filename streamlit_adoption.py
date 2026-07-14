"""
IDP Adoption & Usage Analytics
==============================
Management-facing page: all-time usage of the IDP (DevOps Portal) plus a
year-over-year adoption comparison.

Two lenses, stated explicitly so the numbers can't be misread:

  * USAGE  (volume)  — how MUCH the portal is used: interactions, sessions.
  * ADOPTION (breadth) — how MANY distinct people use it, measured against a
    target population (the number of engineers who *could* be using the IDP).

"Last year vs this year" comparisons are computed on the SAME calendar window
(Jan 1 → today's date, both years) so a partial current year is never compared
against a full previous one. Full-year totals are shown separately.

Reads the same `session_states` Postgres table as the Activity Monitor page.
"""

import streamlit as st
import pandas as pd
import pytz
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

# Your DB utilities (provided on the deployment server)
from utils.postgres import query_to_df

# ------------------------------------------------------------
# 🎨 Page Setup
# ------------------------------------------------------------
st.set_page_config(
    page_title="IDP Adoption & Usage",
    layout="wide",
    page_icon="🚀",
)

# ------------------------------------------------------------
# 🎨 Theme / Styling  (consistent with the Activity Monitor page)
# ------------------------------------------------------------
# Validated categorical palette — fixed slot order, never cycled.
C_THIS_YEAR = "#2a78d6"   # blue    — current year / primary series
C_AQUA      = "#1baf7a"   # aqua    — secondary series (returning users)
C_AMBER     = "#eda100"   # yellow  — tertiary
C_RED       = "#e34948"   # red     — quaternary
C_LAST_YEAR = "#94a3b8"   # de-emphasis gray — prior year (context, not a hue slot)
C_OLD_YEARS = "#cbd5e1"   # lighter gray — years before last (context)
C_GOOD      = "#006300"   # delta ↑ good (text)
C_BAD       = "#d03b3b"   # delta ↓ bad (text)
SEQ_BLUES   = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

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
    background: linear-gradient(90deg, #2a78d6, #6da7ec);
}
.metric-card.accent-green::before { background: linear-gradient(90deg, #1baf7a, #0ca30c); }
.metric-card.accent-amber::before { background: linear-gradient(90deg, #eda100, #d97706); }
.metric-card.accent-gray::before  { background: linear-gradient(90deg, #94a3b8, #cbd5e1); }
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
.delta-up   { color: #006300; font-weight: 600; }
.delta-down { color: #d03b3b; font-weight: 600; }
.delta-na   { color: #94a3b8; font-weight: 600; }

/* verdict banner */
.verdict {
    padding: 16px 20px;
    border-radius: 12px;
    font-size: 1.02rem;
    margin: 14px 0 20px;
    border: 1px solid;
}
.verdict.up   { background: #f0fdf4; border-color: #a7f3d0; color: #065f46; }
.verdict.down { background: #fef2f2; border-color: #fecaca; color: #7f1d1d; }
.verdict.flat { background: #f8fafc; border-color: #e2e8f0; color: #334155; }
.verdict .big { font-size: 1.5rem; font-weight: 700; }

/* adoption meter */
.meter-wrap { margin: 6px 0 2px; }
.meter-track {
    height: 14px; border-radius: 8px; background: #eef2f7;
    position: relative; overflow: visible;
}
.meter-fill {
    height: 100%; border-radius: 8px;
    background: linear-gradient(90deg, #6da7ec, #2a78d6);
}
.meter-marker {
    position: absolute; top: -4px; width: 3px; height: 22px;
    background: #64748b; border-radius: 2px;
}
.meter-labels {
    display: flex; justify-content: space-between;
    font-size: 0.78rem; color: #64748b; margin-top: 6px;
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
LOCAL_TZ = "Africa/Cairo"
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner="Loading all-time session data…")
def load_all_time_data() -> pd.DataFrame:
    """Load the FULL session_states history (no time bound — this page is
    about all-time trends, unlike the Activity Monitor's rolling window)."""
    query = """
        SELECT timestamp, username, original_user, current_page, session_id
        FROM session_states
    """
    df = query_to_df(query, {})
    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(LOCAL_TZ)
    # fillna BEFORE astype: on newer pandas (string/Arrow dtypes) astype(str)
    # PRESERVES missing values — a NA username then vanishes from groupby keys
    # while still appearing in unique(), crashing strict .loc lookups later.
    df["username"] = (
        df["username"].fillna("unknown").astype(str).str.strip().str.lower()
        .replace({"": "unknown", "none": "unknown", "nan": "unknown", "<na>": "unknown"})
    )
    df["original_user"] = df.get("original_user", None)
    df["current_page"] = df.get("current_page", None)
    df["session_id"] = df.get(
        "session_id", pd.util.hash_pandas_object(df["timestamp"], index=False)
    )
    df["is_assumed"] = (
        df["original_user"].notna()
        & (df["original_user"] != df["username"])
        & (df["original_user"] != "None")
    )

    # Time-derived columns (DST-safe: drop tz before deriving)
    ts_naive = df["timestamp"].dt.tz_localize(None)
    df["year"] = ts_naive.dt.year
    df["month_num"] = ts_naive.dt.month
    df["month"] = ts_naive.dt.to_period("M").dt.to_timestamp()
    df["quarter"] = ts_naive.dt.to_period("Q").astype(str)
    df["day"] = ts_naive.dt.floor("D")
    df["dayofyear"] = ts_naive.dt.dayofyear
    return df


def pct_change(current, previous):
    """% change, or None when there is no baseline."""
    if previous is None or previous == 0 or pd.isna(previous):
        return None
    return (current - previous) / previous * 100.0


def delta_html(pct, positive_is_good=True):
    if pct is None:
        return '<span class="delta-na">— no baseline</span>'
    arrow = "▲" if pct >= 0 else "▼"
    good = (pct >= 0) == positive_is_good
    cls = "delta-up" if good else "delta-down"
    return f'<span class="{cls}">{arrow} {abs(pct):.1f}% YoY</span>'


def metric_card(label, value, sub_html="", accent=""):
    accent_cls = f" accent-{accent}" if accent else ""
    st.markdown(f"""
    <div class="metric-card{accent_cls}">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        <div class="metric-sub">{sub_html}</div>
    </div>""", unsafe_allow_html=True)


def style_fig(fig):
    fig.update_layout(**PLOTLY_LAYOUT)
    fig.update_xaxes(showgrid=True, gridcolor="#f1f5f9", zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f1f5f9", zeroline=False)
    return fig


def year_color(y, this_year, last_year):
    if y == this_year:
        return C_THIS_YEAR
    if y == last_year:
        return C_LAST_YEAR
    return C_OLD_YEARS


def yoy_overlay_chart(df_all, value_fn, title, y_title, this_year, last_year):
    """Monthly line per year on a shared Jan→Dec axis. Current year in blue,
    last year in gray, older years as thin light context lines."""
    fig = go.Figure()
    for y in sorted(df_all["year"].unique()):
        sub = df_all[df_all["year"] == y]
        monthly = value_fn(sub)  # Series indexed by month_num
        monthly = monthly.reindex(range(1, 13))
        is_focus = y in (this_year, last_year)
        fig.add_trace(go.Scatter(
            x=MONTH_NAMES,
            y=monthly.values,
            mode="lines+markers" if is_focus else "lines",
            name=str(y),
            line=dict(
                color=year_color(y, this_year, last_year),
                width=3 if y == this_year else (2.5 if y == last_year else 1.5),
            ),
            marker=dict(size=8),
            connectgaps=False,
        ))
    fig.update_layout(
        title=title, height=340,
        xaxis_title="Month", yaxis_title=y_title,
        legend_title_text="Year",
    )
    return style_fig(fig)


# ------------------------------------------------------------
# 🏷️ Header
# ------------------------------------------------------------
st.markdown("""
<div style="margin-bottom: 8px;">
    <h1 style="margin: 0; font-weight: 700; color: #0f172a;">🚀 IDP Adoption & Usage Analytics</h1>
    <p style="margin: 0; color: #64748b; font-size: 0.95rem;">
        All-time usage of the DevOps Portal and year-over-year adoption — built for management review.
    </p>
</div>
""", unsafe_allow_html=True)

with st.expander("ℹ️ How to read this page — definitions"):
    st.markdown("""
- **Usage (volume)** — how *much* the portal is used: total **interactions**
  (every page event logged) and **sessions** (distinct browser sessions).
- **Adoption (breadth)** — how *many distinct people* use it. An **active user**
  is anyone with ≥ 1 interaction in the period.
- **Adoption rate** = active users ÷ **target population** (the headcount that
  *could* be using the IDP — set it in the controls below; it defaults to the
  all-time distinct user count, so override it with the real eligible headcount
  for a true rate).
- **Same-period YoY** — this year Jan 1 → today vs last year Jan 1 → same date,
  so a partial year is never compared against a full one.
- **New user** — first-ever appearance on the portal falls inside the period.
  **Returning user** — was already using the portal before the period.
- **Stickiness (DAU/MAU)** — average daily users ÷ monthly users; higher means
  people come back more often within the month.
    """)

# ------------------------------------------------------------
# 🎛 Controls
# ------------------------------------------------------------
df_raw = load_all_time_data()
if df_raw.empty:
    st.warning("⚠ No session data available yet.")
    st.stop()

cc1, cc2, cc3 = st.columns([1, 1, 2])
with cc1:
    show_devops = st.toggle(
        "Include DevOps members", value=False,
        help="The team that builds the portal. Excluded by default so adoption reflects real customers.",
    )
with cc2:
    include_assumed = st.toggle(
        "Include assumed sessions", value=False,
        help="Sessions where an admin assumed another user's identity.",
    )

df = df_raw
if not show_devops:
    df = df[~df["username"].isin(DEVOPS_USERNAMES)]
if not include_assumed:
    df = df[~df["is_assumed"]]

if df.empty:
    st.warning("⚠ No data matches the current filters.")
    st.stop()

alltime_users = int(df["username"].nunique())
with cc3:
    target_population = st.number_input(
        "🎯 Target population (eligible users)",
        min_value=1, value=alltime_users, step=1,
        help="How many people COULD be using the IDP (e.g. engineering headcount). "
             "Defaults to all-time distinct users — override with the real number "
             "for a meaningful adoption rate.",
    )

# ------------------------------------------------------------
# ⏱ Period bookkeeping
# ------------------------------------------------------------
now_local = datetime.now(pytz.timezone(LOCAL_TZ))
THIS_YEAR = now_local.year
LAST_YEAR = THIS_YEAR - 1
today_doy = now_local.timetuple().tm_yday

first_event = df["timestamp"].min()
last_event = df["timestamp"].max()
months_live = max(
    (THIS_YEAR - first_event.year) * 12 + (now_local.month - first_event.month) + 1, 1
)

# First-ever appearance per user (computed on the filtered population,
# across ALL time — needed to classify new vs returning correctly)
first_seen = df.groupby("username")["timestamp"].min().dt.tz_localize(None)
first_seen_year = first_seen.dt.year
first_seen_doy = first_seen.dt.dayofyear

# Same-period masks: Jan 1 → today's day-of-year, each year
ytd_mask = df["dayofyear"] <= today_doy
df_ty = df[(df["year"] == THIS_YEAR) & ytd_mask]
df_ly = df[(df["year"] == LAST_YEAR) & ytd_mask]
has_last_year = not df[df["year"] == LAST_YEAR].empty


def period_stats(pdf: pd.DataFrame, year: int) -> dict:
    users = set(pdf["username"].dropna().unique())
    # reindex, not .loc: a username missing from first_seen (e.g. NA-key
    # divergence between groupby and unique) counts as not-new instead of
    # crashing the page with 'not in index'
    new_users = int(
        ((first_seen_year == year) & (first_seen_doy <= today_doy))
        .reindex(list(users), fill_value=False).sum()
    ) if users else 0
    return {
        "interactions": len(pdf),
        "users": len(users),
        "sessions": int(pdf["session_id"].nunique()),
        "new_users": new_users,
        "pages": int(pdf["current_page"].nunique()),
        "user_set": users,
    }


ty = period_stats(df_ty, THIS_YEAR)
ly = period_stats(df_ly, LAST_YEAR)

# ============================================================
# 📈 TABS
# ============================================================
tab_exec, tab_trends, tab_adoption = st.tabs([
    "🎯 Executive Summary",
    "📈 Usage Trends (YoY)",
    "👥 Adoption & Retention",
])

# ------------------------------------------------------------
# 🎯 TAB 1 — EXECUTIVE SUMMARY
# ------------------------------------------------------------
with tab_exec:
    # ---- Verdict banner -----------------------------------
    users_pct = pct_change(ty["users"], ly["users"]) if has_last_year else None
    vol_pct = pct_change(ty["interactions"], ly["interactions"]) if has_last_year else None

    if users_pct is None:
        st.markdown(f"""
        <div class="verdict flat">
            📌 Not enough history for a year-over-year comparison yet — data starts
            <b>{first_event.strftime('%b %Y')}</b>. All-time figures below.
        </div>""", unsafe_allow_html=True)
    else:
        direction = "up" if users_pct >= 0 else "down"
        word = "grew" if users_pct >= 0 else "declined"
        vol_word = "up" if (vol_pct or 0) >= 0 else "down"
        st.markdown(f"""
        <div class="verdict {direction}">
            <span class="big">IDP adoption {word} {abs(users_pct):.1f}% year-over-year.</span><br>
            {ty['users']} active users Jan 1 – {now_local.strftime('%b %d')} {THIS_YEAR}
            vs {ly['users']} in the same period {LAST_YEAR}.
            Usage volume is {vol_word} {abs(vol_pct or 0):.1f}%
            ({ty['interactions']:,} vs {ly['interactions']:,} interactions).
        </div>""", unsafe_allow_html=True)

    # ---- All-time KPI row ----------------------------------
    st.markdown("#### 🏛 All-Time (since launch)")
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        metric_card("🔁 Total Interactions", f"{len(df):,}",
                    f"since {first_event.strftime('%b %d, %Y')}")
    with a2:
        metric_card("👥 All-Time Users", f"{alltime_users:,}",
                    f"{alltime_users / target_population * 100:.0f}% of target population ever used it",
                    accent="green")
    with a3:
        metric_card("🗂 Total Sessions", f"{df['session_id'].nunique():,}",
                    f"~{df['session_id'].nunique() / months_live:.0f} sessions / month",
                    accent="amber")
    with a4:
        metric_card("📅 Months Live", f"{months_live}",
                    f"last activity {last_event.strftime('%b %d, %Y')}",
                    accent="gray")

    # ---- Same-period YoY tiles -----------------------------
    st.markdown(
        f"#### ⚖️ Same-Period Comparison — Jan 1 → {now_local.strftime('%b %d')} "
        f"({LAST_YEAR} vs {THIS_YEAR})"
    )
    y1, y2, y3, y4 = st.columns(4)
    with y1:
        metric_card("👥 Active Users", f"{ty['users']:,}",
                    f"{delta_html(users_pct)} &nbsp;· {LAST_YEAR}: {ly['users']:,}")
    with y2:
        metric_card("🔁 Interactions", f"{ty['interactions']:,}",
                    f"{delta_html(vol_pct)} &nbsp;· {LAST_YEAR}: {ly['interactions']:,}",
                    accent="green")
    with y3:
        sess_pct = pct_change(ty["sessions"], ly["sessions"]) if has_last_year else None
        metric_card("🗂 Sessions", f"{ty['sessions']:,}",
                    f"{delta_html(sess_pct)} &nbsp;· {LAST_YEAR}: {ly['sessions']:,}",
                    accent="amber")
    with y4:
        new_pct = pct_change(ty["new_users"], ly["new_users"]) if has_last_year else None
        metric_card("✨ New Users", f"{ty['new_users']:,}",
                    f"{delta_html(new_pct)} &nbsp;· {LAST_YEAR}: {ly['new_users']:,}",
                    accent="gray")

    # ---- Adoption meter ------------------------------------
    st.markdown("#### 🎯 Adoption Rate vs Target Population")
    ty_rate = ty["users"] / target_population * 100
    ly_rate = ly["users"] / target_population * 100 if has_last_year else None
    marker_html = (
        f'<div class="meter-marker" style="left: {min(ly_rate, 100):.1f}%;" '
        f'title="{LAST_YEAR} same period: {ly_rate:.0f}%"></div>'
        if ly_rate is not None else ""
    )
    st.markdown(f"""
    <div class="meter-wrap">
        <div class="meter-track">
            <div class="meter-fill" style="width: {min(ty_rate, 100):.1f}%;"></div>
            {marker_html}
        </div>
        <div class="meter-labels">
            <span><b style="color:#0f172a;">{ty_rate:.0f}%</b> adoption this year
                  ({ty['users']} of {target_population} eligible users)</span>
            <span>{f'grey marker = {LAST_YEAR} same period ({ly_rate:.0f}%)' if ly_rate is not None else ''}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    if target_population == alltime_users:
        st.caption(
            "⚠️ Target population is defaulting to the all-time user count — set the real "
            "eligible headcount in the controls above for a true adoption rate."
        )

    # ---- Quarterly summary table ---------------------------
    st.markdown("#### 🗓 Quarterly Summary")
    q = (
        df.groupby("quarter")
        .agg(
            interactions=("username", "size"),
            active_users=("username", "nunique"),
            sessions=("session_id", "nunique"),
        )
        .sort_index()
    )
    q["users QoQ %"] = (q["active_users"].pct_change() * 100).round(1)
    q["interactions QoQ %"] = (q["interactions"].pct_change() * 100).round(1)
    q["adoption %"] = (q["active_users"] / target_population * 100).round(1)
    st.dataframe(q, width="stretch")

    # ---- Auto takeaways ------------------------------------
    st.markdown("#### 📝 Key Takeaways")
    takeaways = []
    if users_pct is not None:
        takeaways.append(
            f"**Adoption is {'up' if users_pct >= 0 else 'down'} {abs(users_pct):.1f}% YoY** "
            f"({ly['users']} → {ty['users']} active users, same Jan–{now_local.strftime('%b')} window)."
        )
    if vol_pct is not None:
        takeaways.append(
            f"**Usage volume is {'up' if vol_pct >= 0 else 'down'} {abs(vol_pct):.1f}% YoY** "
            f"({ly['interactions']:,} → {ty['interactions']:,} interactions) — "
            + ("engagement per user is deepening."
               if (users_pct is not None and vol_pct > users_pct)
               else "growth is driven by breadth more than depth.")
        )
    takeaways.append(
        f"**{ty_rate:.0f}% of the target population** ({ty['users']}/{target_population}) "
        f"has used the IDP so far this year."
    )
    if ty["new_users"]:
        takeaways.append(
            f"**{ty['new_users']} first-time users** onboarded this year"
            + (f" vs {ly['new_users']} by this point last year." if has_last_year else ".")
        )
    if has_last_year:
        retained = len(ty["user_set"] & ly["user_set"])
        if ly["users"]:
            takeaways.append(
                f"**{retained / ly['users'] * 100:.0f}% retention** — {retained} of "
                f"{ly['users']} of last year's users are still active this year."
            )
    for t in takeaways:
        st.markdown(f"- {t}")

# ------------------------------------------------------------
# 📈 TAB 2 — USAGE TRENDS (YoY)
# ------------------------------------------------------------
with tab_trends:
    st.markdown("#### 📈 Overall Usage vs Time — year overlay")
    st.caption(
        f"Each line is one calendar year on a shared Jan→Dec axis. "
        f"**{THIS_YEAR} in blue**, {LAST_YEAR} in gray, earlier years as light context."
    )

    t1, t2 = st.columns(2)
    with t1:
        fig_vol = yoy_overlay_chart(
            df,
            lambda sub: sub.groupby("month_num").size(),
            "🔁 Monthly Interactions (volume)", "Interactions",
            THIS_YEAR, LAST_YEAR,
        )
        st.plotly_chart(fig_vol, width="stretch")
    with t2:
        fig_mau = yoy_overlay_chart(
            df,
            lambda sub: sub.groupby("month_num")["username"].nunique(),
            "👥 Monthly Active Users (breadth)", "Active users",
            THIS_YEAR, LAST_YEAR,
        )
        st.plotly_chart(fig_mau, width="stretch")

    # ---- Year × month heatmap ------------------------------
    st.markdown("#### 🗓 Usage Intensity — year × month")
    heat = df.groupby(["year", "month_num"]).size().reset_index(name="interactions")
    heat_pivot = (
        heat.pivot(index="year", columns="month_num", values="interactions")
        .reindex(columns=range(1, 13))
        .sort_index(ascending=False)
    )
    fig_heat = px.imshow(
        heat_pivot,
        labels=dict(x="Month", y="Year", color="Interactions"),
        x=MONTH_NAMES,
        y=[str(y) for y in heat_pivot.index],
        color_continuous_scale=SEQ_BLUES,
        aspect="auto",
        text_auto=True,
    )
    fig_heat.update_layout(height=90 + 60 * len(heat_pivot))
    st.plotly_chart(style_fig(fig_heat), width="stretch")

    # ---- Cumulative adoption curve -------------------------
    st.markdown("#### 📈 Cumulative Adoption Curve")
    st.caption("Every distinct person who has EVER used the IDP, over time — the classic adoption S-curve.")
    cum_users = (
        first_seen.dt.to_period("M").dt.to_timestamp()
        .value_counts().sort_index().cumsum()
    )
    monthly_new = pd.DataFrame({
        "month": cum_users.index,
        "cumulative_users": cum_users.values,
    })
    fig_cum = go.Figure()
    fig_cum.add_trace(go.Scatter(
        x=monthly_new["month"], y=monthly_new["cumulative_users"],
        mode="lines+markers", name="Cumulative users",
        fill="tozeroy",
        line=dict(color=C_THIS_YEAR, width=3),
        fillcolor="rgba(42,120,214,0.12)",
        marker=dict(size=7),
    ))
    fig_cum.add_hline(
        y=target_population, line_dash="dash", line_color="#64748b",
        annotation_text=f"Target population ({target_population})",
        annotation_font_color="#64748b",
    )
    fig_cum.update_layout(
        height=360, xaxis_title="Month", yaxis_title="Distinct users (all-time)",
        showlegend=False,
    )
    st.plotly_chart(style_fig(fig_cum), width="stretch")

    with st.expander("📋 Monthly data table"):
        monthly_table = (
            df.groupby("month")
            .agg(
                interactions=("username", "size"),
                active_users=("username", "nunique"),
                sessions=("session_id", "nunique"),
            )
            .sort_index()
        )
        monthly_table["adoption %"] = (
            monthly_table["active_users"] / target_population * 100
        ).round(1)
        st.dataframe(monthly_table, width="stretch")

# ------------------------------------------------------------
# 👥 TAB 3 — ADOPTION & RETENTION
# ------------------------------------------------------------
with tab_adoption:
    # ---- Monthly adoption rate -----------------------------
    st.markdown("#### 🎯 Monthly Adoption Rate")
    st.caption("Monthly active users as a % of the target population.")
    mau = df.groupby("month")["username"].nunique().rename("mau").reset_index()
    mau["adoption_pct"] = mau["mau"] / target_population * 100
    fig_rate = px.line(
        mau, x="month", y="adoption_pct", markers=True,
        template=PLOTLY_TEMPLATE,
    )
    fig_rate.update_traces(line=dict(color=C_THIS_YEAR, width=3), marker=dict(size=8))
    fig_rate.update_layout(
        height=320, xaxis_title="Month", yaxis_title="Adoption rate (%)",
        yaxis_ticksuffix="%",
    )
    st.plotly_chart(style_fig(fig_rate), width="stretch")

    # ---- New vs returning ----------------------------------
    st.markdown("#### ✨ New vs Returning Users per Month")
    st.caption("New = first-ever month on the portal. Returning = used it in an earlier month.")
    user_month = df.groupby(["month", "username"]).size().reset_index(name="n")
    user_first_month = first_seen.dt.to_period("M").dt.to_timestamp()
    user_month["kind"] = [
        "New" if user_first_month.get(u) == m else "Returning"
        for m, u in zip(user_month["month"], user_month["username"])
    ]
    nvr = user_month.groupby(["month", "kind"])["username"].nunique().reset_index(name="users")
    fig_nvr = px.bar(
        nvr, x="month", y="users", color="kind",
        template=PLOTLY_TEMPLATE,
        color_discrete_map={"New": C_THIS_YEAR, "Returning": C_AQUA},
        category_orders={"kind": ["Returning", "New"]},
    )
    fig_nvr.update_traces(marker_line_color="#ffffff", marker_line_width=2)
    fig_nvr.update_layout(
        height=340, xaxis_title="Month", yaxis_title="Users",
        legend_title_text="", barmode="stack",
    )
    st.plotly_chart(style_fig(fig_nvr), width="stretch")

    # ---- Retention & stickiness tiles ----------------------
    st.markdown("#### 🔒 Retention & Stickiness")
    r1, r2, r3, r4 = st.columns(4)

    retained_n = len(ty["user_set"] & ly["user_set"]) if has_last_year else 0
    retention_pct = retained_n / ly["users"] * 100 if (has_last_year and ly["users"]) else None

    def stickiness(year):
        sub = df[df["year"] == year]
        if sub.empty:
            return None
        dau = sub.groupby("day")["username"].nunique().mean()
        mau_avg = sub.groupby("month")["username"].nunique().mean()
        return dau / mau_avg * 100 if mau_avg else None

    stick_ty, stick_ly = stickiness(THIS_YEAR), stickiness(LAST_YEAR)
    months_per_user = df.groupby("username")["month"].nunique().mean()
    depth_ty = ty["interactions"] / ty["users"] if ty["users"] else 0
    depth_ly = ly["interactions"] / ly["users"] if ly["users"] else None

    with r1:
        metric_card(
            "🔒 YoY Retention",
            f"{retention_pct:.0f}%" if retention_pct is not None else "n/a",
            f"{retained_n} of {ly['users']} {LAST_YEAR} users still active"
            if has_last_year else "needs last-year data",
        )
    with r2:
        metric_card(
            "📊 Stickiness (DAU/MAU)",
            f"{stick_ty:.0f}%" if stick_ty is not None else "n/a",
            f"{LAST_YEAR}: {stick_ly:.0f}%" if stick_ly is not None else "within-month return rate",
            accent="green",
        )
    with r3:
        metric_card(
            "🔁 Interactions / User",
            f"{depth_ty:.0f}",
            f"{delta_html(pct_change(depth_ty, depth_ly))} &nbsp;· depth of engagement"
            if depth_ly else "this year, per active user",
            accent="amber",
        )
    with r4:
        metric_card(
            "📅 Active Months / User",
            f"{months_per_user:.1f}",
            "all-time average per user",
            accent="gray",
        )

    # ---- Page-level adoption -------------------------------
    st.markdown("#### 📚 Page-Level Adoption — which features pull users in?")
    st.caption(
        f"Distinct users per page, {LAST_YEAR} vs {THIS_YEAR} (same Jan 1 → "
        f"{now_local.strftime('%b %d')} window). Ranked by this year."
    )
    page_ty = df_ty.groupby("current_page")["username"].nunique()
    page_ly = df_ly.groupby("current_page")["username"].nunique()
    top_pages = page_ty.sort_values(ascending=False).head(10)
    if top_pages.empty:
        st.info("No page activity recorded this year yet.")
    else:
        page_cmp = pd.DataFrame({
            "page": top_pages.index,
            str(THIS_YEAR): top_pages.values,
            str(LAST_YEAR): [int(page_ly.get(p, 0)) for p in top_pages.index],
        })
        page_long = page_cmp.melt(id_vars="page", var_name="year", value_name="users")
        fig_pages = px.bar(
            page_long, x="users", y="page", color="year",
            orientation="h", barmode="group",
            template=PLOTLY_TEMPLATE,
            color_discrete_map={str(THIS_YEAR): C_THIS_YEAR, str(LAST_YEAR): C_LAST_YEAR},
            category_orders={"year": [str(LAST_YEAR), str(THIS_YEAR)]},
        )
        fig_pages.update_layout(
            height=420, yaxis_title=None, xaxis_title="Distinct users",
            legend_title_text="Year",
            yaxis=dict(categoryorder="total ascending"),
        )
        st.plotly_chart(style_fig(fig_pages), width="stretch")

        with st.expander("📋 Page adoption table"):
            page_cmp["Δ users"] = page_cmp[str(THIS_YEAR)] - page_cmp[str(LAST_YEAR)]
            st.dataframe(page_cmp.set_index("page"), width="stretch")
