import streamlit as st

from src.theme import set_theme
from src import tasks_repo


set_theme(page_title="Best Streamlit Website", page_icon="🌐")


@st.cache_data(ttl=30)
def _task_kpis():
    try:
        tasks_repo.init_db()
        tasks = tasks_repo.get_all_tasks()
    except Exception:
        tasks = []

    total = len(tasks)
    by_status = {}
    for t in tasks:
        s = (t.get("status") or "Unknown").strip() or "Unknown"
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "total": total,
        "done": by_status.get("Done", 0),
        "in_progress": by_status.get("In-Progress", 0) + by_status.get("In Progress", 0),
        "review": by_status.get("Review", 0),
        "backlog": by_status.get("Backlog", 0),
    }


@st.fragment
def render_quick_kpis():
    header = st.columns([1, 0.25])
    with header[0]:
        st.subheader("Quick snapshot")
        st.caption("Fast KPIs powered by caching + fragments (refresh only updates this section).")
    with header[1]:
        if st.button("Refresh", use_container_width=True):
            _task_kpis.clear()  # type: ignore[attr-defined]

    k = _task_kpis()
    cols = st.columns(4)
    cols[0].metric("Total tasks", k["total"])
    cols[1].metric("In progress", k["in_progress"])
    cols[2].metric("In review", k["review"])
    cols[3].metric("Done", k["done"])


st.markdown(
    """
    <style>
    .bsw-hero {
        background: linear-gradient(120deg, rgba(224,234,252,0.95) 0%, rgba(207,222,243,0.95) 100%);
        border: 1px solid rgba(30, 41, 59, 0.08);
        border-radius: 18px;
        box-shadow: 0 10px 34px rgba(2, 8, 23, 0.12);
        padding: 2.0rem 1.8rem 1.4rem 1.8rem;
        max-width: 1100px;
        margin: 1.5rem auto 1.0rem auto;
    }
    .bsw-hero h1 {
        font-size: 2.4rem;
        font-weight: 800;
        margin: 0 0 0.25rem 0;
        color: #0b63d6;
        letter-spacing: 0.6px;
    }
    .bsw-hero p {
        margin: 0.35rem 0 0 0;
        color: #334155;
        font-size: 1.0rem;
        line-height: 1.45;
    }
    .bsw-chip {
        display: inline-block;
        padding: 0.16rem 0.6rem;
        border-radius: 999px;
        background: rgba(37,99,235,0.10);
        border: 1px solid rgba(37,99,235,0.22);
        color: #1d4ed8;
        font-weight: 600;
        font-size: 0.78rem;
        margin-right: 0.35rem;
        margin-top: 0.45rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="bsw-hero">
      <h1>Best Streamlit Website</h1>
      <p>A fast, modern multipage app with task management, scheduling, and an AI playground for MCP + agents.</p>
      <div>
        <span class="bsw-chip">Streamlit-first UI</span>
        <span class="bsw-chip">MCP tools</span>
        <span class="bsw-chip">LangChain / LangGraph agents</span>
        <span class="bsw-chip">Kubernetes / Docker</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

render_quick_kpis()

st.divider()

st.subheader("Explore")
st.caption("Jump into a section, or use the left navigation.")

team_tab, ai_tab = st.tabs(["Team Management", "AI Playground"])

with team_tab:
    c1, c2, c3 = st.columns(3)

    with c1:
        with st.container(border=True):
            st.markdown("#### Team Task Manager")
            st.caption("Plan, track, and ship work with a rich Kanban + analytics UI.")
            st.page_link("pages/1_Team_Task_Manager.py", label="Open", icon="📋")

    with c2:
        with st.container(border=True):
            st.markdown("#### DevOps Referral Agent")
            st.caption("Parse CVs and generate structured signals for referral decisions.")
            st.page_link("pages/2_DevOps_Referral_Agent.py", label="Open", icon="🧑‍💼")

    with c3:
        with st.container(border=True):
            st.markdown("#### WFH Schedule")
            st.caption("Two-week rotation planner with validations + full-year grids.")
            st.page_link("pages/3_WFH_Schedule.py", label="Open", icon="📅")

with ai_tab:
    r1c1, r1c2, r1c3 = st.columns(3)
    with r1c1:
        with st.container(border=True):
            st.markdown("#### DataGen Agent")
            st.caption("Generate synthetic datasets with an agent workflow.")
            st.page_link("pages/4_DataGen_Agent.py", label="Open", icon="🧪")

    with r1c2:
        with st.container(border=True):
            st.markdown("#### Agent Management")
            st.caption("Inspect, run, and debug your agents and tool calls.")
            st.page_link("pages/5_Agent_Management.py", label="Open", icon="🧠")

    with r1c3:
        with st.container(border=True):
            st.markdown("#### Kubernetes")
            st.caption("Cluster insights and Helm operations via MCP.")
            st.page_link("pages/6_Kubernetes.py", label="Open", icon="☸️")

    r2c1, r2c2, r2c3 = st.columns(3)
    with r2c1:
        with st.container(border=True):
            st.markdown("#### Scheduler")
            st.caption("Schedule MCP health checks and tool runs (auto-refresh every 10s).")
            st.page_link("pages/10_MCP_Scheduler.py", label="Open", icon="⏱️")

    with r2c2:
        with st.container(border=True):
            st.markdown("#### Setup")
            st.caption("Configure MCP servers and environment connectivity.")
            st.page_link("pages/7_Setup.py", label="Open", icon="🛠️")

    with r2c3:
        with st.container(border=True):
            st.markdown("#### Docker MCP Test")
            st.caption("Test Docker MCP connectivity and basic operations.")
            st.page_link("pages/8_Docker_MCP_Test.py", label="Open", icon="🐳")

    r3c1, r3c2, r3c3 = st.columns(3)
    with r3c1:
        with st.container(border=True):
            st.markdown("#### Nexus Explorer")
            st.caption("Explore Nexus repositories and artifacts.")
            st.page_link("pages/9_Nexus_Explorer.py", label="Open", icon="📦")

    with r3c2:
        st.empty()
    with r3c3:
        st.empty()
