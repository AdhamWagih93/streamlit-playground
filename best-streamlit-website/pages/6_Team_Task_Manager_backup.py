
import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime, date
import uuid
import io
import random

import plotly.express as px
import plotly.graph_objects as go
from src import tasks_repo


# --- Custom CSS for modern, professional look ---
st.markdown(
    """
    <style>
    .ttm-container { max-width:1250px;margin:0 auto;animation:fadeIn .6s ease; }
    h1, .block-container > h1 { background:linear-gradient(120deg,#0b63d6,#6c5ce7,#00b894); -webkit-background-clip:text; color:transparent; animation:hueShift 8s linear infinite, titleFloat 6s ease-in-out infinite; letter-spacing:1px; position:relative; }
    @keyframes hueShift { 0%{filter:hue-rotate(0deg);}100%{filter:hue-rotate(360deg);} }
    @keyframes titleFloat { 0%,100%{text-shadow:0 4px 14px rgba(11,99,214,.35);}50%{text-shadow:0 6px 22px rgba(108,92,231,.55);} }
    @keyframes fadeIn { from{opacity:0;transform:translateY(6px);} to{opacity:1;transform:translateY(0);} }
    @keyframes popIn { 0%{transform:scale(.95);opacity:0;} 100%{transform:scale(1);opacity:1;} }
    @keyframes pulseGlow { 0%{box-shadow:0 0 0 0 rgba(214,48,49,0.55);}70%{box-shadow:0 0 0 12px rgba(214,48,49,0);}100%{box-shadow:0 0 0 0 rgba(214,48,49,0);} }
    @keyframes shimmer { 0%{background-position:-200% 0;}100%{background-position:200% 0;} }
    .ttm-kanban-col { background:linear-gradient(145deg,#ffffff,#f1f6fb); border-radius:16px; box-shadow:0 4px 20px -4px rgba(11,99,214,0.10),0 2px 6px -1px rgba(0,0,0,0.05); padding:1.15rem .75rem .85rem; margin-bottom:1.3rem; min-height:190px; position:relative; overflow:hidden; }
    .ttm-kanban-col:before { content:"";position:absolute;inset:0;background:linear-gradient(120deg,rgba(11,99,214,0.07),rgba(108,92,231,0.05),rgba(0,184,148,0.07));opacity:0;transition:opacity .4s;pointer-events:none; }
    .ttm-kanban-col:hover:before { opacity:1; }
    .ttm-status-header { font-size:1.05rem;font-weight:700;letter-spacing:.6px;margin-bottom:.65rem;padding:.35rem 0;border-radius:10px;color:#fff;text-align:center;text-shadow:0 1px 2px rgba(0,0,0,.25); }
    .ttm-status-Backlog { background:linear-gradient(120deg,#b2bec3,#636e72); }
    .ttm-status-To-Do { background:linear-gradient(120deg,#74b9ff,#0984e3); }
    .ttm-status-In-Progress { background:linear-gradient(120deg,#ffeaa7,#fdcb6e); color:#222; }
    .ttm-status-Review { background:linear-gradient(120deg,#a29bfe,#6c5ce7); }
    .ttm-status-Done { background:linear-gradient(120deg,#55efc4,#00b894); color:#103b2f; }
    .ttm-task-card { background:#fff; border-radius:14px; box-shadow:0 4px 14px -4px rgba(11,99,214,0.18),0 2px 5px -2px rgba(0,0,0,0.08); padding:.85rem .8rem .75rem .85rem; margin-bottom:.9rem; transition:box-shadow .25s, transform .18s; border:1px solid #dce6f1; position:relative; animation:popIn .35s ease; }
    .ttm-task-card:hover { box-shadow:0 8px 26px -6px rgba(11,99,214,0.28),0 4px 10px -4px rgba(0,0,0,0.12); transform:translateY(-3px); }
    .ttm-overdue { border:1px solid #d63031 !important; animation:pulseGlow 2.6s infinite; }
    .ttm-overdue-badge { position:absolute; top:6px; right:8px; background:linear-gradient(90deg,#d63031,#b71c1c); color:#fff; font-size:0.62rem; font-weight:700; padding:2px 6px; border-radius:12px; letter-spacing:0.5px; box-shadow:0 0 0 1px #fff,0 2px 4px rgba(0,0,0,0.15); }
    .ttm-task-meta span.ttm-overdue-date { color:#b71c1c;font-weight:600; }
    .ttm-task-title { font-size:1.04rem;font-weight:600;color:#0b2140;margin-bottom:.25rem;line-height:1.2; }
    .ttm-task-meta { color:#6b7b8f;font-size:.9rem;margin-bottom:.25rem;letter-spacing:.2px; }
    .ttm-task-tags { font-size:.8rem;color:#0984e3;margin-bottom:.2rem;font-weight:500; }
    .ttm-priority-badge { display:inline-block;font-size:0.70rem;font-weight:700;border-radius:30px;padding:0.22rem 0.65rem;margin-left:.35rem;margin-bottom:.1rem;color:#fff;letter-spacing:.6px;vertical-align:middle;text-transform:uppercase;background-size:220% 100%;background-position:0 0;animation:shimmer 5s linear infinite;box-shadow:0 0 0 1px rgba(255,255,255,0.6),0 3px 6px -2px rgba(0,0,0,0.25); }
    .ttm-priority-Low { background:linear-gradient(120deg,#00b894,#55efc4,#00b894); }
    .ttm-priority-Medium { background:linear-gradient(120deg,#0984e3,#74b9ff,#0984e3); }
    .ttm-priority-High { background:linear-gradient(120deg,#fdcb6e,#e17055,#fdcb6e); color:#222; }
    .ttm-priority-Critical { background:linear-gradient(120deg,#d63031,#ff5f56,#b71c1c); color:#fff; border:2px solid #b71c1c; box-shadow:0 0 0 2px #fff,0 0 10px 2px #d63031; }
    .ttm-btn-row { display:flex; gap:.5rem; margin-top:.3rem; }
    .ttm-checkline { font-size:0.7rem; margin-top:.35rem; display:flex; align-items:center; gap:6px; color:#35506b; }
    .ttm-checkbar { flex:1; background:#e6edf5; border-radius:4px; height:6px; position:relative; overflow:hidden; }
    .ttm-checkbar-fill { position:absolute; left:0; top:0; bottom:0; background:linear-gradient(90deg,#00b894,#0b63d6); }
    div[data-testid="column"] button { background:linear-gradient(135deg,#0b63d6,#6c5ce7,#00b894); background-size:200% 100%; color:#fff; border:none; border-radius:10px; padding:.42rem 1.1rem; font-size:.75rem; font-weight:600; cursor:pointer; transition:background .35s, transform .18s, box-shadow .25s; box-shadow:0 4px 14px -4px rgba(11,99,214,.45); margin-bottom:.35rem; letter-spacing:.55px; position:relative; overflow:hidden; }
    div[data-testid="column"] button:hover { background-position:100% 0; transform:translateY(-2px); box-shadow:0 8px 24px -6px rgba(11,99,214,.60); }
    .ttm-inline-btn-row { display:flex; gap:.35rem; margin:-.15rem 0 .45rem 0; }
    .ttm-inline-btn-row button { flex:1; }
    .ttm-move-prev { background:linear-gradient(135deg,#6c5ce7,#0b63d6) !important; }
    .ttm-move-next { background:linear-gradient(135deg,#00b894,#0b63d6) !important; }
    .ttm-edit-btn { background:linear-gradient(135deg,#fdcb6e,#e17055) !important; color:#222 !important; }
    .ttm-del-btn { background:linear-gradient(135deg,#d63031,#b71c1c) !important; }
    .ttm-add-pop-btn { background:linear-gradient(135deg,#0b63d6,#6c5ce7,#00b894); background-size:200% 100%; padding:.45rem .9rem; font-size:.68rem; border-radius:8px; font-weight:700; letter-spacing:.7px; }
    .ttm-add-pop-btn:hover { background-position:100% 0; }
    .ttm-detail { background:#f7fafd; border-radius:14px; box-shadow:0 2px 12px rgba(11,99,214,0.06); padding:1.2rem 1.2rem .7rem 1.2rem; margin-bottom:1.2rem; }
    .ttm-detail-title { font-size:1.35rem;font-weight:700;color:#0b63d6;margin-bottom:.5rem; }
    .ttm-detail-label { color:#51658a;font-size:.95rem;font-weight:600;margin-top:.7rem; text-transform:uppercase; letter-spacing:.5px; }
    .ttm-detail-value { color:#222;font-size:.98rem;margin-bottom:.2rem; }
    .ttm-detail-history, .ttm-detail-comments { font-size:.85rem;color:#6b7b8f;margin-bottom:.2rem; }
    .ttm-kpi-box { background:linear-gradient(145deg,#ffffff,#eef4fa); border-radius:14px; box-shadow:0 4px 14px -4px rgba(11,99,214,0.18),0 2px 5px -2px rgba(0,0,0,0.08); padding:1.15rem .9rem; text-align:center; position:relative; overflow:hidden; }
    .ttm-kpi-box:before { content:""; position:absolute; inset:0; background:linear-gradient(120deg,rgba(11,99,214,0.08),rgba(108,92,231,0.06),rgba(0,184,148,0.1)); opacity:0; transition:opacity .4s; }
    .ttm-kpi-box:hover:before { opacity:1; }
    .ttm-kpi-label { color:#51658a;font-size:.75rem;font-weight:700;letter-spacing:.6px;text-transform:uppercase; }
    .ttm-kpi-value { font-size:1.65rem;font-weight:700;color:#0b63d6;line-height:1; }
    .ttm-kpi-bad { color:#d63031 !important; }
    .ttm-kpi-warn { color:#e17055 !important; }
    .ttm-kpi-good { color:#00b894 !important; }
    .ttm-kpi-bar { position:relative;height:6px;background:#e6edf5;border-radius:4px;margin-top:10px;overflow:hidden; }
    .ttm-kpi-bar-fill { position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,#00b894,#0b63d6); }
    .ttm-filters-bar { background:linear-gradient(145deg,#ffffff,#f2f7fb); padding:.75rem 1.05rem .55rem 1.05rem; border-radius:20px; box-shadow:0 6px 22px -6px rgba(11,99,214,0.25),0 3px 9px -3px rgba(0,0,0,0.08); margin-bottom:1.3rem; animation:fadeIn .5s ease; position:relative; overflow:visible; }
    .ttm-filters-bar:before { content:""; position:absolute; inset:-2px; border-radius:22px; background:linear-gradient(120deg,rgba(11,99,214,.25),rgba(108,92,231,.22),rgba(0,184,148,.25)); filter:blur(14px); opacity:.55; z-index:-1; }
    .ttm-filter-grid { display:flex; flex-wrap:wrap; gap:.65rem 1.1rem; align-items:flex-end; }
    .ttm-filter-grid > div { flex:1 1 160px; }
    .ttm-filter-label { font-size:.65rem; font-weight:700; letter-spacing:.6px; color:#51658a; text-transform:uppercase; margin-bottom:2px; }
    .ttm-filter-badge { background:#0b63d6; color:#fff; font-size:.65rem; padding:2px 7px; border-radius:14px; margin-left:6px; letter-spacing:.5px; }
    .ttm-section-gap { margin-top:1.1rem; }
    div[data-baseweb="tab-list"] { gap:.4rem; }
    button[role="tab"] { background:linear-gradient(140deg,#eef4fa,#ffffff); border:1px solid #d0dce8 !important; box-shadow:0 1px 4px rgba(11,99,214,0.08); border-radius:12px !important; padding:.55rem 1.1rem !important; font-weight:600 !important; color:#35506b !important; transition:all .25s !important; }
    button[role="tab"][aria-selected="true"] { background:linear-gradient(120deg,#0b63d6,#6c5ce7,#00b894); color:#fff !important; box-shadow:0 4px 14px -4px rgba(11,99,214,0.5); }
    button[role="tab"]:hover { transform:translateY(-2px); }
    ::-webkit-scrollbar { width:10px; }
    ::-webkit-scrollbar-track { background:#f0f5fa; }
    ::-webkit-scrollbar-thumb { background:linear-gradient(#0b63d6,#6c5ce7); border-radius:20px; }
    /* Backlog horizontal strip styling */
    .ttm-backlog-strip { display:flex; gap:.9rem; padding:.55rem .65rem .4rem .65rem; overflow-x:auto; scroll-snap-type:x mandatory; background:linear-gradient(145deg,#ffffff,#f2f7fb); border:1px solid #d0dce8; border-radius:20px; box-shadow:0 5px 20px -6px rgba(11,99,214,.18),0 2px 8px -3px rgba(0,0,0,.08); }
    .ttm-backlog-strip::-webkit-scrollbar { height:10px; }
    .ttm-backlog-strip::-webkit-scrollbar-track { background:transparent; }
    .ttm-backlog-strip::-webkit-scrollbar-thumb { background:linear-gradient(90deg,#0b63d6,#6c5ce7,#00b894); border-radius:20px; }
    .ttm-backlog-item { min-width:250px; max-width:260px; flex:0 0 auto; scroll-snap-align:start; }
    .ttm-backlog-item .ttm-task-card { margin-bottom:.4rem; }
    /* Subtle hover lift for backlog grouping */
    .ttm-backlog-item .ttm-task-card:hover { box-shadow:0 10px 28px -8px rgba(11,99,214,0.40),0 4px 12px -5px rgba(0,0,0,0.18); }
    @media (max-width: 1100px){ .ttm-backlog-item { min-width:220px; max-width:230px; } }
    /* (Removed horizontal board styles; restoring vertical Kanban columns) */
    </style>
    """,
    unsafe_allow_html=True,
)

# Page config
st.set_page_config(page_title="Team Task Manager", page_icon="📋", layout="wide")

tasks_repo.init_db()

# ----- Utilities -----
STATUS_ORDER = ["Backlog", "To Do", "In Progress", "Review", "Done"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]


def load_tasks():
    return tasks_repo.get_all_tasks()


def save_tasks(_tasks):
    # no-op now; individual operations persist immediately via repo
    pass


def new_task_dict(title, description, assignee, priority, due_date, estimates, tags, reporter, reviewer=None):
    return {
        "id": str(uuid.uuid4()),
        "title": title.strip(),
        "description": description.strip(),
        "assignee": assignee or "Unassigned",
        "reporter": reporter,
        "reviewer": reviewer,
        "priority": priority,
        "status": "Backlog",
        "created_at": datetime.utcnow().isoformat(),
        "due_date": due_date.isoformat() if isinstance(due_date, (date, datetime)) else None,
        "estimates_hours": estimates,
        "tags": tags,
        "comments": [],
        "history": [
            {"when": datetime.utcnow().isoformat(), "what": "created", "by": reporter or "system"}
        ],
    }


# ----- Safe helpers -----

def tasks_to_df(tasks):
    if not tasks:
        return pd.DataFrame(columns=["id", "title", "assignee", "priority", "status", "due_date", "estimates_hours", "tags"])
    df = pd.json_normalize(tasks)
    if "due_date" in df.columns:
        df["due_date"] = pd.to_datetime(df["due_date"], errors="coerce").dt.date
    return df


# ----- Initialize session state -----
if "tasks_cache" not in st.session_state:
    st.session_state.tasks_cache = load_tasks()

if "users" not in st.session_state:
    # initial sample users; user can edit
    st.session_state.users = ["Alice", "Bob", "Carol", "Dave"]

# Add a small sample dataset if empty
if not st.session_state.tasks_cache:
    sample = [
        new_task_dict("Onboard new hire", "Prepare environment and docs", "Alice", "High", date.today(), 4, ["onboarding"], reporter="System"),
        new_task_dict("Q3 Roadmap", "Finalize objectives", "Bob", "Medium", date.today(), 8, ["planning"], reporter="System"),
        new_task_dict("Bug #432: login error", "Intermittent login failures in auth module", "Carol", "Critical", date.today(), 6, ["bug"], reporter="System"),
    ]
    for t in sample:
        tasks_repo.create_task(t)
    st.session_state.tasks_cache = load_tasks()


# ----- Layout -----

# --- Main container ---
st.markdown('<div class="ttm-container">', unsafe_allow_html=True)
st.title("Team Task Manager — Ultimate")
st.markdown("<span style='color:#51658a;font-size:1.1rem;'>Professional task management built into your Streamlit site.</span>", unsafe_allow_html=True)

# Top-level controls (compact bar)
st.markdown('<div class="ttm-filters-bar">', unsafe_allow_html=True)
# View mode (My vs Team) row
# Initialize state defaults before creating widgets to avoid post-instantiation assignment errors
if 'my_view' not in st.session_state:
    st.session_state.my_view = True
if 'current_user' not in st.session_state:
    st.session_state.current_user = st.session_state.users[0] if st.session_state.users else 'Me'
if 'username' not in st.session_state:
    st.session_state.username = st.session_state.current_user
vmc1, vmc2 = st.columns([0.5,1.5])
with vmc1:
    st.toggle("My View", value=st.session_state.get('my_view', True), key='my_view')
with vmc2:
    st.text_input("Impersonate User", value=st.session_state.get('current_user', (st.session_state.users[0] if st.session_state.users else 'Me')), key='current_user')
    st.session_state.username = st.session_state.current_user

fc1, fc2, fc3, fc4 = st.columns([2.2,1.1,1.1,0.8])
with fc1:
    search = st.text_input("Search (title / desc / tag)", placeholder="Type to filter…")
with fc2:
    assignee_filter = st.selectbox("Assignee", options=["All"] + st.session_state.users, index=0)
with fc3:
    priority_filter = st.selectbox("Priority", options=["All"] + PRIORITIES, index=0)
with fc4:
    refresh_clicked = st.button("↻", help="Refresh from DB")
    if refresh_clicked:
        st.session_state.tasks_cache = load_tasks()
        st.toast("Tasks refreshed", icon="✅")
st.markdown('</div>', unsafe_allow_html=True)

### Inline creation integrated into the board (old global creator removed) ###


# Management: quick user add
with st.sidebar.expander("Team & Settings", expanded=False):
    st.subheader("Team members")
    new_user = st.text_input("Add team member")
    if st.button("Add member") and new_user.strip():
        st.session_state.users.append(new_user.strip())
        st.success("Member added")
    st.write(st.session_state.users)
    st.markdown("---")
    if st.button("Refresh tasks from DB"):
        st.session_state.tasks_cache = load_tasks()
        st.success("Refreshed")
    st.markdown("---")
    st.markdown("**Sample Data**")
    bulk_clear = st.checkbox("Clear existing before generating", key="bulk-clear")
    if st.button("Generate 30 Sample Tasks", key="generate-30"):
        # optional clear
        if bulk_clear:
            for existing in st.session_state.tasks_cache:
                tasks_repo.delete_task(existing['id'])
            st.session_state.tasks_cache = []
        # Pools
        sample_titles = [
            "Implement OAuth", "Fix race condition", "Refactor utils", "Improve logging", "Add unit tests",
            "Optimize query", "Design landing page", "Bug: null pointer", "Migrate schema", "Document API",
            "Enhance security", "Clean dead code", "CI pipeline update", "Dashboard polish", "Integrate payments",
            "Accessibility audit", "Cache layer", "Feature toggle system", "Onboarding flow", "Email template revamp",
            "Autoscaling config", "Improve error UX", "Session timeout handling", "Add dark mode", "Data export csv",
            "Real-time metrics", "Websocket reconnect", "Input validation", "Secrets rotation", "Upgrade dependencies",
            "Pagination backend", "Load test suite", "SLA monitoring", "Geo replication", "Add feature flags",
            "Alert thresholds", "Failover drill", "Background jobs retry", "API rate limiting", "Refine search relevance",
            "Add fuzzy search", "Bulk import tool", "User impersonation", "Notification digest", "GDPR data delete",
            "Profile avatar crop", "Wizard step UX", "Reduce bundle size", "Improve mobile layout", "Investigate memory leak"
        ]
        tag_pool = ["backend", "frontend", "infra", "security", "performance", "ui", "api", "db", "etl", "ops"]
        checklist_samples = [
            ["Spec drafted", "Spec approved", "Implemented", "Code reviewed", "Deployed"],
            ["Reproduce issue", "Add test", "Fix code", "Verify fix"],
            ["Design", "Prototype", "Feedback", "Refine", "Ship"],
        ]
        for i in range(30):
            title = sample_titles[i % len(sample_titles)] + f" #{i+1}"
            prio = random.choices(PRIORITIES, weights=[3,5,4,2])[0]
            assignee = random.choice(st.session_state.users + [None, None])
            due = date.today() + pd.Timedelta(days=random.randint(-5, 30))
            est = round(random.uniform(1, 16), 1)
            tags = random.sample(tag_pool, k=random.randint(1, min(3, len(tag_pool))))
            base = new_task_dict(title, f"Autogenerated sample task {i+1}", assignee, prio, due, est, tags, reporter=st.session_state.username)
            # Random status advancement
            target_status = random.choices(STATUS_ORDER, weights=[4,5,5,3,2])[0]
            if target_status != base['status']:
                base['status'] = target_status
                base['history'].append({"when": datetime.utcnow().isoformat(), "what": f"status->{target_status}", "by": "seed"})
            # Occasionally add checklist
            if random.random() < 0.55:
                template = random.choice(checklist_samples)
                base['checklist'] = [
                    {"id": str(uuid.uuid4()), "text": item, "done": (random.random() < 0.5)}
                    for item in template
                ]
            tasks_repo.create_task(base)
        st.session_state.tasks_cache = load_tasks()
        st.success("Generated 30 sample tasks")
        st.rerun()

# Establish scope according to view mode
if st.session_state.get('my_view'):
    scope_tasks = [t for t in st.session_state.tasks_cache if t.get('assignee') == st.session_state.get('current_user')]
else:
    scope_tasks = st.session_state.tasks_cache

# Filter tasks (start from scoped set)
filtered = scope_tasks
if search:
    s = search.lower()
    filtered = [t for t in filtered if s in t.get("title","").lower() or s in t.get("description"," ").lower() or any(s in tag.lower() for tag in (t.get("tags") or []))]
if assignee_filter and assignee_filter != "All":
    filtered = [t for t in filtered if (t.get("assignee") or "Unassigned") == assignee_filter]
if priority_filter and priority_filter != "All":
    filtered = [t for t in filtered if t.get("priority") == priority_filter]

# Convert to DataFrame for visualizations
df = tasks_to_df(scope_tasks)

total_tasks = len(scope_tasks)
open_tasks = len([t for t in scope_tasks if t.get("status") != "Done"])
overdue = len([t for t in scope_tasks if t.get("due_date") and pd.to_datetime(t.get("due_date")) < pd.Timestamp(date.today()) and t.get("status") != "Done"])
completed_pct = round(len([t for t in scope_tasks if t.get("status") == "Done"]) / total_tasks * 100, 1) if total_tasks else 0
open_pct = round(open_tasks / total_tasks * 100, 1) if total_tasks else 0

def pct_class(p):
    if p >= 75: return 'ttm-kpi-good'
    if p >= 40: return 'ttm-kpi-warn'
    return 'ttm-kpi-bad'

overdue_cls = 'ttm-kpi-bad' if overdue > 0 else 'ttm-kpi-good'
completed_cls = pct_class(completed_pct)
open_cls = pct_class(100-open_pct)  # inverse logic for remaining risk

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown('<div class="ttm-kpi-box">', unsafe_allow_html=True)
    st.markdown('<div class="ttm-kpi-label">Total Tasks</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ttm-kpi-value">{total_tasks}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
with k2:
    st.markdown('<div class="ttm-kpi-box">', unsafe_allow_html=True)
    st.markdown('<div class="ttm-kpi-label">Open Tasks</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ttm-kpi-value {open_cls}">{open_tasks}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
with k3:
    st.markdown('<div class="ttm-kpi-box">', unsafe_allow_html=True)
    st.markdown('<div class="ttm-kpi-label">Overdue</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ttm-kpi-value {overdue_cls}">{overdue}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
with k4:
    st.markdown('<div class="ttm-kpi-box">', unsafe_allow_html=True)
    st.markdown('<div class="ttm-kpi-label">Completed</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ttm-kpi-value {completed_cls}">{completed_pct}%</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ttm-kpi-bar"><div class="ttm-kpi-bar-fill" style="width:{completed_pct}%;"></div></div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

## Reduced extra dividers for a cleaner top section

# Prepare card HTML for each task (preserving all highlights and theme)
def make_card_html(t):
    priority = t.get("priority", "Medium")
    # Determine overdue (due date before today and not Done)
    overdue = False
    due_raw = t.get("due_date")
    if due_raw and t.get('status') != 'Done':
        try:
            due_dt = pd.to_datetime(due_raw).date()
            if due_dt < date.today():
                overdue = True
        except Exception:
            pass
    badge_html = f'<span class="ttm-priority-badge ttm-priority-{priority}">{priority}</span>'
    overdue_cls = ' ttm-overdue' if overdue else ''
    overdue_badge = '<div class="ttm-overdue-badge">OVERDUE</div>' if overdue else ''
    # Build card
    card_html = f'<div class="ttm-task-card{overdue_cls}">{overdue_badge}<div class="ttm-task-title">{t.get("title")} {badge_html}</div>'
    due_fragment = t.get("due_date") or "—"
    if overdue and due_fragment != "—":
        due_fragment = f'<span class="ttm-overdue-date">{due_fragment}</span>'
    card_html += f'<div class="ttm-task-meta">👤 {t.get("assignee") or "Unassigned"} | 📅 {due_fragment}</div>'
    if t.get("tags"):
        card_html += f'<div class="ttm-task-tags">🏷️ {", ".join(t.get("tags"))}</div>'
    if t.get("estimates_hours"):
        card_html += f'<div class="ttm-task-meta">⏱️ {t.get("estimates_hours")}h</div>'
    # Checklist progress (done/total + remaining) if checklist exists
    cl = t.get('checklist') or []
    if cl:
        done = sum(1 for c in cl if c.get('done'))
        total = len(cl)
        remaining = total - done
        pct = int((done/total)*100) if total else 0
        card_html += (
            f"<div class='ttm-checkline'>"
            f"<span>☑ {done}/{total} ({remaining} open)</span>"
            f"<div class='ttm-checkbar'><div class='ttm-checkbar-fill' style='width:{pct}%;'></div></div>"
            f"</div>"
        )
    card_html += '</div>'
    return card_html







board_tab, analytics_tab, report_tab, io_tab = st.tabs(["🗂 Board", "📊 Analytics", "📨 Report", "📁 Import / Export"])

with board_tab:
    st.markdown('<div class="ttm-section-gap"></div>', unsafe_allow_html=True)
    st.subheader("Kanban Board")

    # Kanban columns including Backlog (original layout) --------------------------------------
    statuses = STATUS_ORDER
    kanban_cols = st.columns(len(statuses))
    priority_rank = {"Critical":4, "High":3, "Medium":2, "Low":1}
    kanban_data = {status: [t for t in filtered if t.get("status") == status] for status in statuses}
    for status, items in kanban_data.items():
        items.sort(key=lambda t: (
            -priority_rank.get(t.get("priority"), 0),
            t.get("due_date") or "9999-12-31",
            t.get("title", "")
        ))
    for idx, status in enumerate(statuses):
        with kanban_cols[idx]:
            status_class = f"ttm-status-{status.replace(' ', '-') }"
            header_cols = st.columns([5,1])
            with header_cols[0]:
                st.markdown(f'<div class="ttm-status-header {status_class}">{status}</div>', unsafe_allow_html=True)
            with header_cols[1]:
                # Add Task popover (index for quick access)
                with st.popover(f"➕ {idx+1}", use_container_width=True):
                    st.markdown(f"#### New Task in {status}")
                    nt_title = st.text_input("Title", key=f"nt-title-{status}")
                    nt_desc = st.text_area("Description", key=f"nt-desc-{status}")
                    nt_assignee = st.selectbox("Assignee", ["(none)"]+st.session_state.users, index=0, key=f"nt-assignee-{status}")
                    nt_priority = st.selectbox("Priority", PRIORITIES, index=1, key=f"nt-priority-{status}")
                    nt_due = st.date_input("Due", value=date.today(), key=f"nt-due-{status}")
                    nt_est = st.number_input("Estimate (h)", min_value=0.0, value=1.0, step=0.5, key=f"nt-est-{status}")
                    nt_tags_raw = st.text_input("Tags (comma, optional)", key=f"nt-tags-{status}")
                    nt_reviewer = st.selectbox("Reviewer (optional)", ["(none)"]+st.session_state.users, index=0, key=f"nt-reviewer-{status}")
                    if st.button("Create", key=f"nt-create-{status}"):
                        if nt_title.strip():
                            tags_list = [tg.strip() for tg in nt_tags_raw.split(',') if tg.strip()]
                            base = new_task_dict(
                                nt_title,
                                nt_desc,
                                None if nt_assignee=="(none)" else nt_assignee,
                                nt_priority,
                                nt_due,
                                nt_est,
                                tags_list,
                                reporter=st.session_state.username,
                                reviewer=None if nt_reviewer=="(none)" else nt_reviewer,
                            )
                            if base['status'] != status:
                                base['status'] = status
                                base['history'].append({"when": datetime.utcnow().isoformat(), "what": f"status->{status}", "by": st.session_state.username})
                            tasks_repo.create_task(base)
                            st.session_state.tasks_cache = load_tasks()
                            st.success("Created")
                            st.rerun()
            for t in kanban_data[status]:
                tid = t['id']
                card_html = make_card_html(t)
                st.markdown(card_html[:-6], unsafe_allow_html=True)
                btn_cols = st.columns([1,1,1,1])
                with btn_cols[0]:
                    if idx > 0 and st.button("← Prev", key=f"prev-{tid}"):
                        tasks_repo.update_task_status(tid, statuses[idx-1], by=st.session_state.username)
                        st.session_state.tasks_cache = load_tasks()
                        st.rerun()
                with btn_cols[1]:
                    if idx < len(statuses)-1 and st.button("Next →", key=f"next-{tid}"):
                        tasks_repo.update_task_status(tid, statuses[idx+1], by=st.session_state.username)
                        st.session_state.tasks_cache = load_tasks()
                        st.rerun()
                with btn_cols[2]:
                    if st.button("✏️ Edit", key=f"edit-inline-{tid}"):
                        st.session_state[f"inline_edit_{tid}"] = not st.session_state.get(f"inline_edit_{tid}")
                        st.rerun()
                with btn_cols[3]:
                    if st.button("🗑️", key=f"delete-{tid}"):
                        tasks_repo.delete_task(tid)
                        st.session_state.tasks_cache = load_tasks()
                        st.rerun()
                if st.session_state.get(f"inline_edit_{tid}"):
                    with st.container():
                        st.markdown("<hr style='margin:0.4rem 0 0.6rem 0;'>", unsafe_allow_html=True)
                        task_live = tasks_repo.get_task(tid) or t
                        et1, et2 = st.columns([2,1])
                        with et1:
                            ntitle = st.text_input("Title", value=task_live.get('title',''), key=f"it-title-{tid}")
                        with et2:
                            npriority = st.selectbox("Priority", PRIORITIES, index=PRIORITIES.index(task_live.get('priority','Medium')) if task_live.get('priority') in PRIORITIES else 1, key=f"it-prio-{tid}")
                        ndesc = st.text_area("Description", value=task_live.get('description',''), key=f"it-desc-{tid}")
                        col_meta = st.columns(4)
                        with col_meta[0]:
                            nassignee = st.selectbox("Assignee", ["(none)"]+st.session_state.users, index=0 if not task_live.get('assignee') else (st.session_state.users.index(task_live.get('assignee'))+1 if task_live.get('assignee') in st.session_state.users else 0), key=f"it-assignee-{tid}")
                        with col_meta[1]:
                            ndue = st.date_input("Due", value=pd.to_datetime(task_live.get('due_date')).date() if task_live.get('due_date') else date.today(), key=f"it-due-{tid}")
                        with col_meta[2]:
                            nest = st.number_input("Est (h)", min_value=0.0, value=float(task_live.get('estimates_hours') or 1.0), step=0.5, key=f"it-est-{tid}")
                        with col_meta[3]:
                            nstatus = st.selectbox("Status", STATUS_ORDER, index=STATUS_ORDER.index(task_live.get('status','Backlog')), key=f"it-status-{tid}")
                        ntags = st.text_input("Tags (comma)", value=", ".join(task_live.get('tags') or []), key=f"it-tags-{tid}")
                        st.markdown("**Checklist**")
                        if task_live.get('checklist'):
                            for ci in task_live.get('checklist'):
                                cid = ci.get('id')
                                ccols = st.columns([0.07,0.78,0.15])
                                with ccols[0]:
                                    chk = st.checkbox("", value=ci.get('done', False), key=f"it-cl-{cid}")
                                with ccols[1]:
                                    st.markdown(("~~"+ci.get('text','')+"~~") if chk else ci.get('text',''))
                                with ccols[2]:
                                    if st.button("🗑", key=f"it-delcl-{cid}"):
                                        tasks_repo.delete_check_item(tid, cid)
                                        st.session_state.tasks_cache = load_tasks()
                                        st.rerun()
                                if chk != ci.get('done'):
                                    tasks_repo.toggle_check_item(tid, cid, chk)
                                    st.session_state.tasks_cache = load_tasks()
                                    st.rerun()
                        new_inline_ci = st.text_input("Add checklist item", key=f"it-new-ci-{tid}")
                        if st.button("Add Item", key=f"it-add-ci-{tid}") and new_inline_ci.strip():
                            tasks_repo.add_check_item(tid, new_inline_ci.strip())
                            st.session_state.tasks_cache = load_tasks()
                            st.rerun()
                        # Reviewer selection during inline edit
                        reviewer_value = task_live.get('reviewer') or ''
                        reviewer_choices = ["(none)"] + st.session_state.users
                        reviewer_sel = st.selectbox("Reviewer", reviewer_choices, index=reviewer_choices.index(reviewer_value) if reviewer_value in reviewer_choices else 0, key=f"it-rev-{tid}")
                        ab1, ab2, ab3 = st.columns([1,1,1])
                        with ab1:
                            if st.button("💾 Save", key=f"it-save-{tid}"):
                                history = (tasks_repo.get_task(tid) or {}).get('history', [])
                                history.append({"when": datetime.utcnow().isoformat(), "what": "edited", "by": st.session_state.username})
                                reviewer_final = None if reviewer_sel == "(none)" else reviewer_sel
                                tasks_repo.update_task({
                                    'id': tid,
                                    'title': ntitle,
                                    'description': ndesc,
                                    'assignee': None if nassignee == "(none)" else nassignee,
                                    'priority': npriority,
                                    'due_date': ndue.isoformat(),
                                    'estimates_hours': nest,
                                    'tags': [x.strip() for x in ntags.split(',') if x.strip()],
                                    'status': nstatus,
                                    'history': history,
                                    'reporter': task_live.get('reporter'),
                                    'reviewer': reviewer_final,
                                })
                                st.session_state.tasks_cache = load_tasks()
                                st.session_state[f"inline_edit_{tid}"] = False
                                st.success("Saved")
                                st.rerun()
                        with ab2:
                            if st.button("❌ Cancel", key=f"it-cancel-{tid}"):
                                st.session_state[f"inline_edit_{tid}"] = False
                                st.rerun()
                        with ab3:
                            if st.button("💬 Comment", key=f"it-comment-open-{tid}"):
                                st.session_state[f"show_comments_{tid}"] = not st.session_state.get(f"show_comments_{tid}")
                        if st.session_state.get(f"show_comments_{tid}"):
                            st.markdown("**Comments**")
                            for c in (task_live.get('comments') or [])[-15:]:
                                st.write(f"- {c.get('when')}: {c.get('by')}: {c.get('text')}")
                            new_c = st.text_input("New comment", key=f"it-new-comment-{tid}")
                            if st.button("Post", key=f"it-post-comment-{tid}") and new_c.strip():
                                tasks_repo.add_comment(tid, new_c.strip(), by=st.session_state.username)
                                st.session_state.tasks_cache = load_tasks()
                                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

with analytics_tab:
    st.subheader("Insight Dashboard ✨")
    if df.empty:
        st.info("No tasks available for analytics yet (current view scope).")
    else:
        # --- Consolidated compact layout ---
        status_colors = {"Backlog": "#636e72","To Do": "#0984e3","In Progress": "#fdcb6e","Review": "#6c5ce7","Done": "#00b894"}
        priority_colors = {"Low": "#55efc4","Medium": "#74b9ff","High": "#e17055","Critical": "#d63031"}

        # Build data pieces once
        status_priority = df.groupby(['status','priority']).size().reset_index(name='count')
        pivot_status_prio = status_priority.pivot(index='status', columns='priority', values='count').reindex(STATUS_ORDER).fillna(0)
        hs = df.groupby(['priority','status']).size().reset_index(name='count')
        assignee_df = df.copy(); assignee_df['assignee'] = assignee_df['assignee'].fillna('Unassigned')
        aw = assignee_df.groupby(['assignee','status']).size().reset_index(name='count')
        assignees_order = aw.groupby('assignee')['count'].sum().sort_values(ascending=False).index.tolist()
        pivot_aw = aw.pivot(index='assignee', columns='status', values='count').reindex(assignees_order).fillna(0)

        # Heatmap prep
        heat = df.groupby(['priority','status']).size().reset_index(name='count')
        matrix = []
        for p in PRIORITIES:
            row = []
            for s in STATUS_ORDER:
                val = heat[(heat.priority==p) & (heat.status==s)]['count']
                row.append(int(val.iloc[0]) if not val.empty else 0)
            matrix.append(row)
        crit_index = PRIORITIES.index('Critical')
        base_matrix = [r[:] for r in matrix]
        base_matrix[crit_index] = [0]*len(STATUS_ORDER)
        critical_matrix = [[None]*len(STATUS_ORDER) for _ in PRIORITIES]
        critical_matrix[crit_index] = matrix[crit_index]
        text_matrix = [[(f"🔥 {c}" if (ri==crit_index and c>0) else str(c)) for c in row] for ri,row in enumerate(matrix)]

        # Row 1 (three small charts)
        r1c1, r1c2, r1c3 = st.columns([1.05,1,1.15])
        with r1c1:
            st.markdown("**Status vs Priority**")
            fig_stack = go.Figure()
            for p in PRIORITIES:
                if p in pivot_status_prio.columns:
                    fig_stack.add_bar(x=pivot_status_prio.index, y=pivot_status_prio[p], name=p, marker_color=priority_colors.get(p,'#999'))
            fig_stack.update_layout(barmode='stack', template='plotly_white', height=300, margin=dict(l=6,r=6,t=28,b=20), legend=dict(orientation='h', yanchor='bottom', y=1.01, xanchor='right', x=1))
            st.plotly_chart(fig_stack, use_container_width=True)
        with r1c2:
            st.markdown("**Hierarchy**")
            fig_sun = px.sunburst(hs, path=['priority','status'], values='count', color='priority', color_discrete_map=priority_colors)
            fig_sun.update_layout(margin=dict(t=10,l=0,r=0,b=0), height=300, legend=dict(orientation='h'))
            st.plotly_chart(fig_sun, use_container_width=True)
        with r1c3:
            st.markdown("**Priority Heatmap**")
            base_colorscale = [[0.0, '#f0f6ff'],[0.15, '#d6e8ff'],[0.3, '#b0d4ff'],[0.45, '#85bbff'],[0.6, '#589eff'],[0.75, '#2c7ee6'],[0.9, '#0f5fb3'],[1.0, '#06376a']]
            critical_colorscale = [[0.0, '#ffe5e5'],[0.25, '#ffb3b3'],[0.5, '#ff8080'],[0.75, '#ff4d4d'],[1.0, '#b30000']]
            fig_heat = go.Figure()
            fig_heat.add_trace(go.Heatmap(z=base_matrix, x=STATUS_ORDER, y=PRIORITIES, colorscale=base_colorscale, zmin=0, zmax=max([max(r) if r else 0 for r in matrix]) or 1, showscale=False))
            fig_heat.add_trace(go.Heatmap(z=critical_matrix, x=STATUS_ORDER, y=PRIORITIES, colorscale=critical_colorscale, showscale=True, zmin=0, zmax=max(matrix[crit_index]) or 1, hovertemplate="Priority=%{y}<br>Status=%{x}<br>Critical=%{z}<extra></extra>", opacity=0.95, text=text_matrix, texttemplate="%{text}", textfont=dict(color='black', size=11)))
            for ci, val in enumerate(matrix[crit_index]):
                if val > 0:
                    fig_heat.add_shape(type='rect', x0=ci-0.5, x1=ci+0.5, y0=crit_index-0.5, y1=crit_index+0.5, line=dict(color='#b30000', width=2))
            fig_heat.update_layout(margin=dict(l=8,r=8,t=28,b=24), height=300, template='plotly_white', yaxis=dict(autorange='reversed'))
            st.plotly_chart(fig_heat, use_container_width=True)

        # Row 2 (two wider charts)
        r2c1, r2c2 = st.columns([1,1])
        with r2c1:
            st.markdown("**Assignee Workload**")
            fig_aw = go.Figure()
            for st_status in STATUS_ORDER:
                if st_status in pivot_aw.columns:
                    fig_aw.add_bar(y=pivot_aw.index, x=pivot_aw[st_status], name=st_status, orientation='h', marker_color=status_colors.get(st_status,'#888'))
            fig_aw.update_layout(barmode='stack', template='plotly_white', margin=dict(l=8,r=8,t=28,b=20), legend=dict(orientation='h', yanchor='bottom', y=1.01, xanchor='right', x=1), xaxis_title='Tasks', yaxis_title='', height=340)
            st.plotly_chart(fig_aw, use_container_width=True)
        with r2c2:
            st.markdown("**Burndown**")
            timeline_df = df.copy(); timeline_df['created_at'] = pd.to_datetime(timeline_df.get('created_at'), errors='coerce')
            done_dates = []
            for tsk in scope_tasks:
                for h in (tsk.get('history') or []):
                    if h.get('what','').startswith('status->Done'):
                        done_dates.append(pd.to_datetime(h.get('when')))
                        break
            if not timeline_df['created_at'].isna().all():
                start = timeline_df['created_at'].min().normalize()
                end = pd.Timestamp('today').normalize()
                if done_dates:
                    end = max(end, max(pd.Series(done_dates).dropna()).normalize())
                idx = pd.date_range(start, end, freq='D')
                created_series = timeline_df.set_index('created_at').sort_index()
                created_counts = created_series['id'].groupby(pd.Grouper(freq='D')).count().reindex(idx, fill_value=0).cumsum()
                done_series = pd.Series(1, index=pd.to_datetime(done_dates)).groupby(pd.Grouper(freq='D')).count().reindex(idx, fill_value=0).cumsum() if done_dates else pd.Series(0, index=idx)
                remaining = created_counts - done_series
                daily_completion_rate = done_series.diff().mean()
                projection_x, projection_y = [], []
                if daily_completion_rate and daily_completion_rate > 0 and remaining.iloc[-1] > 0:
                    days_to_zero = int(remaining.iloc[-1] / daily_completion_rate)+1
                    proj_idx = pd.date_range(idx[-1] + pd.Timedelta(days=1), periods=days_to_zero, freq='D')
                    for i, d in enumerate(proj_idx, start=1):
                        val = max(remaining.iloc[-1] - daily_completion_rate * i, 0)
                        projection_x.append(d)
                        projection_y.append(val)
                fig_burn = go.Figure()
                fig_burn.add_trace(go.Scatter(x=idx, y=created_counts, name='Created', line=dict(color='#0b63d6', width=2)))
                fig_burn.add_trace(go.Scatter(x=idx, y=done_series, name='Done', line=dict(color='#00b894', width=2)))
                fig_burn.add_trace(go.Scatter(x=idx, y=remaining, name='Remaining', line=dict(color='#d63031', width=2)))
                if projection_x:
                    fig_burn.add_trace(go.Scatter(x=projection_x, y=projection_y, name='Projected', line=dict(color='#fdcb6e', width=2, dash='dash')))
                fig_burn.update_layout(template='plotly_white', margin=dict(l=8,r=8,t=28,b=24), legend=dict(orientation='h', yanchor='bottom', y=1.01, xanchor='right', x=1), yaxis_title='Tasks', xaxis_title='', hovermode='x unified', height=340)
                st.plotly_chart(fig_burn, use_container_width=True)
            else:
                st.info("Not enough data yet.")

with report_tab:
    st.subheader("Email / Executive Report Preview")
    if df.empty:
        st.info("No tasks to summarize (current view scope).")
    else:
        # Rebuild key charts (reuse logic) but capture static images (base64) for email embedding
        import base64
        from io import BytesIO
        # 1. Status Distribution stacked bar
        status_priority = df.groupby(['status','priority']).size().reset_index(name='count')
        pivot = status_priority.pivot(index='status', columns='priority', values='count').reindex(STATUS_ORDER).fillna(0)
        fig_stack_email = go.Figure()
        priority_colors = {"Low": "#55efc4","Medium": "#74b9ff","High": "#e17055","Critical": "#d63031"}
        for p in PRIORITIES:
            if p in pivot.columns:
                fig_stack_email.add_bar(x=pivot.index, y=pivot[p], name=p, marker_color=priority_colors.get(p,'#999'))
        fig_stack_email.update_layout(barmode='stack', template='plotly_white', margin=dict(l=10,r=10,t=30,b=10), showlegend=True, height=320)
        # 2. Assignee workload horizontal
        assignee_df = df.copy(); assignee_df['assignee'] = assignee_df['assignee'].fillna('Unassigned')
        aw = assignee_df.groupby(['assignee','status']).size().reset_index(name='count')
        assignees_order = aw.groupby('assignee')['count'].sum().sort_values(ascending=False).index.tolist()
        pivot_aw = aw.pivot(index='assignee', columns='status', values='count').reindex(assignees_order).fillna(0)
        status_colors = {"Backlog": "#636e72","To Do": "#0984e3","In Progress": "#fdcb6e","Review": "#6c5ce7","Done": "#00b894"}
        fig_aw_email = go.Figure()
        for st_status in STATUS_ORDER:
            if st_status in pivot_aw.columns:
                fig_aw_email.add_bar(y=pivot_aw.index, x=pivot_aw[st_status], name=st_status, orientation='h', marker_color=status_colors.get(st_status,'#888'))
        fig_aw_email.update_layout(barmode='stack', template='plotly_white', margin=dict(l=10,r=10,t=30,b=10), height=420, showlegend=True)
        # 3. Heatmap (critical emphasized) simplified
        heat = df.groupby(['priority','status']).size().reset_index(name='count')
        matrix = []
        for p in PRIORITIES:
            row = []
            for s in STATUS_ORDER:
                val = heat[(heat.priority==p) & (heat.status==s)]['count']
                row.append(int(val.iloc[0]) if not val.empty else 0)
            matrix.append(row)
        fig_heat_email = go.Figure(data=go.Heatmap(z=matrix, x=STATUS_ORDER, y=PRIORITIES, colorscale='Blues', showscale=True))
        fig_heat_email.update_layout(margin=dict(l=10,r=10,t=30,b=10), height=420, template='plotly_white')
        # Export figures to base64 PNG (requires kaleido)
        def fig_to_base64(fig):
            try:
                img_bytes = fig.to_image(format="png", scale=2)
                return base64.b64encode(img_bytes).decode('utf-8')
            except Exception as e:
                return None
        b64_stack = fig_to_base64(fig_stack_email)
        b64_aw = fig_to_base64(fig_aw_email)
        b64_heat = fig_to_base64(fig_heat_email)
        # KPIs
        total_tasks = len(scope_tasks)
        open_tasks = len([t for t in scope_tasks if t.get("status") != "Done"])
        overdue = len([t for t in scope_tasks if t.get("due_date") and pd.to_datetime(t.get("due_date")) < pd.Timestamp(date.today()) and t.get("status") != "Done"])
        done_count = len([t for t in scope_tasks if t.get("status") == "Done"])
        completion_pct = round(done_count/total_tasks*100,1) if total_tasks else 0
        critical_open = len([t for t in scope_tasks if t.get('priority')=='Critical' and t.get('status')!='Done'])
        # Build HTML email body
        report_actor = None
        if st.session_state.get('my_view'):
            report_actor = st.session_state.get('current_user') or None
        elif 'assignee_filter' in locals() and assignee_filter and assignee_filter != 'All':
            report_actor = assignee_filter
        title_text = f"{report_actor} Task Executive Summary" if report_actor else "Team Task Executive Summary"
        email_parts = []
        email_parts.append('<div style="font-family:Segoe UI,Arial,sans-serif;max-width:900px;margin:0 auto;background:#ffffff;border:1px solid #e5edf5;border-radius:14px;padding:32px;">')
        email_parts.append(f'<h1 style="margin:0 0 4px 0;font-size:26px;color:#0b2140;">{title_text}</h1>')
        generated_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        email_parts.append(f'<div style="color:#51658a;font-size:13px;margin-bottom:22px;">Generated {generated_ts}</div>')
        # KPI row
        kpi_style = 'flex:1;background:linear-gradient(145deg,#f7fafd,#eef3f9);border:1px solid #dde6f0;border-radius:12px;padding:14px 16px;'
        def kpi_block(label, value, color='#0b63d6'):
            return f'<div style="{kpi_style}"><div style="font-size:11px;font-weight:700;letter-spacing:.5px;color:#51658a;text-transform:uppercase;">{label}</div><div style="font-size:24px;font-weight:700;color:{color};line-height:1;">{value}</div></div>'
        email_parts.append('<div style="display:flex;gap:14px;margin-bottom:28px;flex-wrap:wrap;">')
        email_parts.append(kpi_block('Total', total_tasks))
        email_parts.append(kpi_block('Open', open_tasks, '#d63031' if open_tasks>10 else ('#e17055' if open_tasks>5 else '#00b894')))
        email_parts.append(kpi_block('Overdue', overdue, '#d63031' if overdue>0 else '#00b894'))
        email_parts.append(kpi_block('Critical Open', critical_open, '#d63031' if critical_open>0 else '#00b894'))
        email_parts.append(kpi_block('Completion', f'{completion_pct}%', '#00b894' if completion_pct>=75 else ('#e17055' if completion_pct>=40 else '#d63031')))
        email_parts.append('</div>')
        # Charts section
        def img_tag(b64, alt):
            return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="width:100%;border:1px solid #e1e8f0;border-radius:12px;margin-bottom:18px;" />' if b64 else f'<div style="font-size:12px;color:#d63031;margin-bottom:18px;">[Missing {alt}]</div>'
        email_parts.append('<h2 style="font-size:20px;margin:0 0 12px 0;color:#0b2140;">Key Visuals</h2>')
        def chart_cell(b64, alt):
            return (
                f'<div style="flex:1;min-width:260px;display:flex;flex-direction:column;">'
                f'<div style="font-size:12px;font-weight:600;color:#35506b;margin:0 0 4px 2px;letter-spacing:.5px;text-transform:uppercase;">{alt}</div>'
                f'{img_tag(b64, alt)}'
                f'</div>'
            )
        email_parts.append('<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px;">')
        email_parts.append(chart_cell(b64_stack, 'Status Distribution'))
        email_parts.append(chart_cell(b64_aw, 'Assignee Workload'))
        email_parts.append(chart_cell(b64_heat, 'Priority vs Status'))
        email_parts.append('</div>')
        # Critical list
        crit_list = [t for t in scope_tasks if t.get('priority')=='Critical' and t.get('status')!='Done']
        email_parts.append('<h2 style="font-size:20px;margin:10px 0 8px 0;color:#0b2140;">Open Critical Items</h2>')
        if crit_list:
            email_parts.append('<ul style="padding-left:18px;margin:4px 0 20px 0;">')
            for t in crit_list:
                due = t.get('due_date') or '—'
                overdue_flag = ' <strong style="color:#d63031;">(OVERDUE)</strong>' if t.get('due_date') and pd.to_datetime(t.get('due_date'))<pd.Timestamp(date.today()) else ''
                email_parts.append(f'<li style="margin:4px 0 6px 0;font-size:14px;line-height:1.25;"><strong>{t.get("title")}</strong>{overdue_flag}<br><span style="color:#51658a;font-size:12px;">Due {due} • {t.get("assignee") or "Unassigned"} • {t.get("status")}</span></li>')
            email_parts.append('</ul>')
        else:
            email_parts.append('<div style="font-size:13px;color:#00b894;margin-bottom:20px;">None 🎉</div>')
        # Open tasks table
        open_tasks_list = [t for t in scope_tasks if t.get('status')!='Done'][:50]
        email_parts.append('<h2 style="font-size:20px;margin:10px 0 8px 0;color:#0b2140;">Open Tasks (Top 50)</h2>')
        if open_tasks_list:
            email_parts.append('<table style="width:100%;border-collapse:collapse;font-size:12px;">')
            email_parts.append('<tr style="background:#0b2140;color:#fff;text-align:left;">'+''.join([f'<th style="padding:6px 8px;font-weight:600;font-size:11px;letter-spacing:.5px;">{h}</th>' for h in ['Title','Priority','Status','Due','Assignee']])+'</tr>')
            for t in open_tasks_list:
                email_parts.append('<tr>'+''.join([
                    f'<td style="padding:5px 8px;border-bottom:1px solid #e6edf5;">{t.get("title")}</td>',
                    f'<td style="padding:5px 8px;border-bottom:1px solid #e6edf5;">{t.get("priority")}</td>',
                    f'<td style="padding:5px 8px;border-bottom:1px solid #e6edf5;">{t.get("status")}</td>',
                    f'<td style="padding:5px 8px;border-bottom:1px solid #e6edf5;">{t.get("due_date") or "—"}</td>',
                    f'<td style="padding:5px 8px;border-bottom:1px solid #e6edf5;">{t.get("assignee") or "Unassigned"}</td>'
                ])+'</tr>')
            email_parts.append('</table>')
        else:
            email_parts.append('<div style="font-size:13px;color:#51658a;">All tasks complete.</div>')
        email_parts.append('<div style="margin-top:32px;font-size:11px;color:#6b7b8f;text-align:center;">Generated automatically • Ready to send</div>')
        email_parts.append('</div>')
        email_html = ''.join(email_parts)
        st.download_button("Download Email HTML", data=email_html.encode('utf-8'), file_name='email_report.html', mime='text/html')
        st.components.v1.html(email_html, height=1500, scrolling=True)

# Handle button actions (simulate popover with Streamlit expander/modal)

# Use new Streamlit query_params API (replaces deprecated experimental_get_query_params)

## Removed bottom edit expander in favor of inline card editing

# Optionally, you can persist the new order in session state or backend if needed
st.markdown("---")

# Inspector / Detail view (modern look)
if '_inspect' in st.session_state:
    tid = st.session_state._inspect
    task = tasks_repo.get_task(tid)
    if task:
        st.markdown('<div class="ttm-detail">', unsafe_allow_html=True)
        st.markdown(f'<div class="ttm-detail-title">{task["title"]}</div>', unsafe_allow_html=True)
        dcol1, dcol2 = st.columns([2,1])
        with dcol1:
            st.markdown('<div class="ttm-detail-label">Description</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="ttm-detail-value">{task.get("description")}</div>', unsafe_allow_html=True)
            st.markdown('<div class="ttm-detail-label">Tags</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="ttm-detail-value">{", ".join(task.get("tags") or [])}</div>', unsafe_allow_html=True)
            st.markdown('<div class="ttm-detail-label">History</div>', unsafe_allow_html=True)
            for h in task.get('history', [])[:20]:
                st.markdown(f'<div class="ttm-detail-history">- {h.get("when")}: {h.get("what")}</div>', unsafe_allow_html=True)
            st.markdown('<div class="ttm-detail-label">Comments</div>', unsafe_allow_html=True)
            for c in task.get('comments', [])[:50]:
                st.markdown(f'<div class="ttm-detail-comments">- {c.get("when")}: {c.get("by")}: {c.get("text")}</div>', unsafe_allow_html=True)
            # Checklist section
            st.markdown('<div class="ttm-detail-label">Checklist</div>', unsafe_allow_html=True)
            if task.get('checklist'):
                for ci in task.get('checklist'):
                    cid = ci.get('id')
                    ccols = st.columns([0.07,0.78,0.15])
                    with ccols[0]:
                        chk = st.checkbox("", value=ci.get('done', False), key=f"cl-{cid}")
                    with ccols[1]:
                        txt = ci.get('text','')
                        st.markdown(("✅ " if chk else "") + (f"~~{txt}~~" if chk else txt))
                    with ccols[2]:
                        if st.button("🗑", key=f"cl-del-{cid}"):
                            tasks_repo.delete_check_item(tid, cid)
                            st.session_state.tasks_cache = load_tasks()
                            st.rerun()
                    if chk != ci.get('done'):
                        tasks_repo.toggle_check_item(tid, cid, chk)
                        st.session_state.tasks_cache = load_tasks()
                        st.rerun()
            new_item = st.text_input("New checklist item", key=f"new-cl-{tid}")
            if st.button("Add Checklist Item", key=f"add-cl-{tid}") and new_item.strip():
                tasks_repo.add_check_item(tid, new_item.strip())
                st.session_state.tasks_cache = load_tasks()
                st.rerun()
            new_comment = st.text_area("Add comment", key=f"comment-{tid}", placeholder="Type your comment and press Enter or click Post")
            if st.button("💬 Post comment", key=f"post-{tid}"):
                if new_comment.strip():
                    tasks_repo.add_comment(tid, new_comment.strip(), by=st.session_state.username)
                    st.session_state.tasks_cache = load_tasks()
                    st.rerun()
        with dcol2:
            st.markdown('<div class="ttm-detail-label">Status</div>', unsafe_allow_html=True)
            new_status = st.selectbox("Status", STATUS_ORDER, index=STATUS_ORDER.index(task.get('status')), key=f"status-{tid}")
            st.markdown('<div class="ttm-detail-label">Assignee</div>', unsafe_allow_html=True)
            new_assignee = st.selectbox("Assignee", options=["(none)"]+st.session_state.users, index=0 if task.get('assignee') is None else (st.session_state.users.index(task.get('assignee'))+1 if task.get('assignee') in st.session_state.users else 0), key=f"assignee-{tid}")
            st.markdown('<div class="ttm-detail-label">Reviewer</div>', unsafe_allow_html=True)
            reviewer_choices = ["(none)"] + st.session_state.users
            reviewer_current = task.get('reviewer') or "(none)"
            new_reviewer = st.selectbox("Reviewer", reviewer_choices, index=reviewer_choices.index(reviewer_current) if reviewer_current in reviewer_choices else 0, key=f"reviewer-{tid}")
            st.markdown(f"<div class='ttm-detail-label'>Reporter</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='ttm-detail-value'>{task.get('reporter') or '(unknown)'}</div>", unsafe_allow_html=True)
            st.markdown('<div class="ttm-detail-label">Priority</div>', unsafe_allow_html=True)
            new_priority = st.selectbox("Priority", PRIORITIES, index=PRIORITIES.index(task.get('priority')) if task.get('priority') in PRIORITIES else 1, key=f"priority-{tid}")
            st.markdown('<div class="ttm-detail-label">Due date</div>', unsafe_allow_html=True)
            new_due = st.date_input("Due date", value=pd.to_datetime(task.get('due_date')).date() if task.get('due_date') else date.today(), key=f"edit-due-date-{tid}")
            if st.button("💾 Save changes", key=f"save-{tid}"):
                current = tasks_repo.get_task(tid) or {}
                history = current.get('history', [])
                history.append({"when": datetime.utcnow().isoformat(), "what": "edited", "by": st.session_state.username})
                reviewer_final = None if new_reviewer == "(none)" else new_reviewer
                tasks_repo.update_task({
                    'id': tid,
                    'status': new_status,
                    'assignee': None if new_assignee == "(none)" else new_assignee,
                    'priority': new_priority,
                    'due_date': new_due.isoformat(),
                    'history': history,
                    'reporter': current.get('reporter'),
                    'reviewer': reviewer_final,
                })
                st.session_state.tasks_cache = load_tasks()
                st.success("Saved")
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

with io_tab:
    st.subheader("Import / Export Tasks")
    st.markdown("Quickly backup or restore tasks in JSON or export a CSV for analysis.")
    ex_col, im_col = st.columns(2)
    with ex_col:
        st.markdown("### Export")
        json_clicked = st.button("Generate JSON Export", key="export-json-btn")
        if json_clicked:
            b = io.BytesIO()
            b.write(json.dumps(st.session_state.tasks_cache, indent=2, ensure_ascii=False).encode('utf-8'))
            b.seek(0)
            st.download_button("Download tasks.json", data=b, file_name="tasks.json", mime="application/json", key="dl-json")
        csv_clicked = st.button("Generate CSV Export", key="export-csv-btn")
        if csv_clicked:
            df_exp = tasks_to_df(st.session_state.tasks_cache)
            csv_data = df_exp.to_csv(index=False).encode('utf-8')
            st.download_button("Download tasks.csv", data=csv_data, file_name="tasks.csv", mime='text/csv', key="dl-csv")
    with im_col:
        st.markdown("### Import")
        st.markdown("Upload a JSON list of tasks previously exported.")
        up = st.file_uploader("Tasks JSON", type=["json"], key="import-json-uploader")
        wipe_first = st.checkbox("Wipe existing tasks before import", value=True, key="wipe-before-import")
        if up is not None:
            try:
                data = json.load(up)
                if isinstance(data, list):
                    if wipe_first:
                        for existing in st.session_state.tasks_cache:
                            tasks_repo.delete_task(existing['id'])
                    imported = 0
                    for t in data:
                        if isinstance(t, dict) and 'id' in t:
                            tasks_repo.create_task(t)
                            imported += 1
                    st.session_state.tasks_cache = load_tasks()
                    st.success(f"Imported {imported} tasks")
                    st.rerun()
                else:
                    st.error("Invalid JSON: expected a list of task objects")
            except Exception as e:
                st.error(f"Failed to import: {e}")
    st.markdown("---")
    st.markdown("#### Format Notes")
    st.markdown("- JSON must be an array of task objects including at least an 'id'. Other missing fields will use defaults.\n- Checklist items, comments, and history arrays are preserved if present.\n- CSV export is one-row-per-task; nested lists are omitted.")

# Quick tips and help
with st.expander("Help & Tips", expanded=False):
    st.markdown(
        """
        - Create tasks with title, description, assignee, priority and due date.
        - Use the Kanban board to move tasks through statuses using the "→ Next" button.
        - View details to comment, edit status, change assignee, or delete.
        - Export tasks to JSON/CSV for reporting or backup.
        - This simple manager stores tasks locally in `best-streamlit-website/data/tasks.json` for convenience.
        - For production use, integrate with a real database (Postgres, Firebase, etc.) and authentication.
        """
    )

# Footer
st.caption("Team Task Manager — concise, secure, and ready for customization")
