
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
    .ttm-status-Closed { background:linear-gradient(120deg,#dfe6e9,#b2bec3); color:#2d3436; }
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
    .ttm-edit-btn { background:linear-gradient(135deg,#ffdd59,#ffa801) !important; color:#222 !important; }
    .ttm-board-col { backdrop-filter: blur(4px); }
    .stButton>button, .stDownloadButton>button { border:1px solid rgba(255,255,255,0.15) !important; }
    .stButton>button:hover { filter:brightness(1.08); box-shadow:0 0 0 2px rgba(255,255,255,0.15) inset; }
    .ttm-status-badge { background:#1e272e; color:#fff; }
    .ttm-status-Deferred { background:linear-gradient(135deg,#485460,#1e272e); border:1px dashed #ffa801; }
    .ttm-deferred-card { border:1px dashed #ffa801 !important; box-shadow:0 0 0 1px rgba(255,168,1,0.25) inset; }
    .ttm-deferred-card .ttm-task-title { color:#ffa801; }
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
FLOW_STATUSES = ["Backlog", "To Do", "In Progress", "Review", "Done"]
DEFERRED_STATUS = "Deferred"
STATUS_ORDER = FLOW_STATUSES + [DEFERRED_STATUS]
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
st.title("Team Task Manager")
#st.markdown("<span style='color:#51658a;font-size:1.1rem;'>Professional task management built into your Streamlit site.</span>", unsafe_allow_html=True)

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
            # Random status advancement (excluding optional Deferred/Closed during seeding)
            target_status = random.choice(["Backlog","To Do","In Progress","Review","Done"])
            if target_status != base['status']:
                ts_when = datetime.utcnow().isoformat()
                base['status'] = target_status
                base['history'].append({"when": ts_when, "what": f"status->{target_status}", "by": "seed"})
                if target_status == 'Done':
                    # Backdate some Done tasks 0-5 days for auto-close demo
                    if random.random() < 0.7:
                        days_ago = random.randint(0,5)
                        done_time = (datetime.utcnow() - pd.Timedelta(days=days_ago)).isoformat()
                        base['done_at'] = done_time
                        base['history'].append({"when": done_time, "what": "status->Done (backdated)", "by": "seed"})
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
# DataFrame AFTER filters for downstream tabs
df = tasks_to_df(filtered)

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
    # Meta compact grid: reporter, assignee, created, due, reviewer (icons compact)
    created_raw = t.get('created_at')
    created_disp = ''
    try:
        if created_raw:
            created_disp = pd.to_datetime(created_raw).date().isoformat()
    except Exception:
        created_disp = ''
    due_fragment = t.get("due_date") or "—"
    if overdue and due_fragment != "—":
        due_fragment = f'<span class="ttm-overdue-date">{due_fragment}</span>'
    reporter = t.get('reporter') or '—'
    assignee = t.get('assignee') or 'Unassigned'
    reviewer = t.get('reviewer') or '—'
    est = t.get('estimates_hours')
    est_html = f' • ⏱️ {est}h' if est else ''
    card_html += (
        f'<div class="ttm-task-meta" style="font-size:.66rem;line-height:1.15em;">'
        f'📝 {reporter} • 👤 {assignee}<br>'
        f'🕒 {created_disp or ""} • 📅 {due_fragment} • 👀 {reviewer}{est_html}'
        f'</div>'
    )
    if t.get("tags"):
        card_html += f'<div class="ttm-task-tags">🏷️ {", ".join(t.get("tags"))}</div>'
    # Checklist progress (always render to keep uniform height)
    cl = t.get('checklist') or []
    done = sum(1 for c in cl if c.get('done'))
    total = len(cl)
    remaining = total - done
    pct = int((done/total)*100) if total else 0
    card_html += (
        f"<div class='ttm-checkline'>"
        f"<span>☑ {done}/{total} ({remaining if total else 0} open)</span>"
        f"<div class='ttm-checkbar'><div class='ttm-checkbar-fill' style='width:{pct}%;'></div></div>"
        f"</div>"
    )
    if t.get('status') == 'Deferred':
        card_html = card_html.replace('ttm-task-card', 'ttm-task-card ttm-deferred-card')
    card_html += '</div>'
    return card_html


board_tab, analytics_tab, report_tab, io_tab, doc_tab = st.tabs(["🗂 Board", "📊 Analytics", "📨 Report", "📁 Import / Export", "📖 Documentation"])

with board_tab:
    st.markdown('<div class="ttm-section-gap"></div>', unsafe_allow_html=True)
    st.subheader("Kanban Board")

    # Kanban columns including Backlog (original layout) --------------------------------------
    # Toggles for optional columns
    if 'show_deferred' not in st.session_state:
        st.session_state.show_deferred = False
    if 'show_closed' not in st.session_state:
        st.session_state.show_closed = False
    with st.sidebar.expander("Board Options", expanded=False):
        st.session_state.show_deferred = st.checkbox("Show 'Deferred' column", value=st.session_state.show_deferred, key="toggle-deferred-col")
        st.session_state.show_closed = st.checkbox("Show 'Closed' column", value=st.session_state.show_closed, key="toggle-closed-col")
    # Build current statuses list
    statuses = ["Backlog", "To Do", "In Progress", "Review", "Done"]
    if st.session_state.show_closed:
        statuses.append("Closed")
    if st.session_state.show_deferred:
        statuses.append("Deferred")
    # Auto-archive: move tasks Done > 2 days to Closed if Closed visible
    if st.session_state.show_closed:
        now_ts = datetime.utcnow()
        changed = False
        for tk in list(st.session_state.tasks_cache):
            if tk.get('status') == 'Done':
                done_at = tk.get('done_at')
                try:
                    done_dt = datetime.fromisoformat(done_at) if done_at else None
                except Exception:
                    done_dt = None
                if not done_dt:
                    for h in reversed(tk.get('history') or []):
                        if h.get('what','').startswith('status->Done'):
                            try:
                                done_dt = datetime.fromisoformat(h.get('when'))
                            except Exception:
                                done_dt = None
                            break
                if done_dt and (now_ts - done_dt).total_seconds() > 2*24*3600:
                    tasks_repo.update_task_status(tk['id'], 'Closed', by=st.session_state.username)
                    changed = True
        if changed:
            st.session_state.tasks_cache = load_tasks()
            # Recompute filtered for subsequent rendering consistency
            filtered = [t for t in st.session_state.tasks_cache if (not search or search.lower() in (t.get('title') or '').lower())]
            if assignee_filter != 'All':
                filtered = [t for t in filtered if t.get('assignee') == assignee_filter]
            if priority_filter != 'All':
                filtered = [t for t in filtered if t.get('priority') == priority_filter]
            if st.session_state.get('my_view'):
                filtered = [t for t in filtered if t.get('assignee') == st.session_state.current_user]
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
                with st.popover(f"➕", use_container_width=True):
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
                # Button bar: Prev | Raise Prio | Edit | Lower Prio | Next
                btn_cols = st.columns([1,1,1,1,1])
                # Prev button (move left) --------------------------------------------------
                with btn_cols[0]:
                    if idx > 0:
                        prev_status = statuses[idx-1]
                        if prev_status not in ('Deferred','Closed'):  # do not move into optional columns via Prev
                            if st.button("←", key=f"prev-{tid}"):
                                tasks_repo.update_task_status(tid, prev_status, by=st.session_state.username)
                                st.session_state.tasks_cache = load_tasks()
                                st.rerun()
                # Priority raise -----------------------------------------------------------
                with btn_cols[1]:
                    cur_p = t.get('priority')
                    if cur_p in PRIORITIES:
                        pi = PRIORITIES.index(cur_p)
                        if pi < len(PRIORITIES)-1:
                            if st.button("↑", help="Raise priority", key=f"prio-up-{tid}"):
                                new_p = PRIORITIES[pi+1]
                                live = tasks_repo.get_task(tid) or t
                                hist = live.get('history', [])
                                hist.append({"when": datetime.utcnow().isoformat(), "what": f"priority->{new_p}", "by": st.session_state.username})
                                tasks_repo.update_task({'id': tid, 'priority': new_p, 'history': hist})
                                st.session_state.tasks_cache = load_tasks()
                                st.rerun()
                        else:
                            st.markdown("<div style='text-align:center;opacity:.35;'>—</div>", unsafe_allow_html=True)
                # Edit (center) ------------------------------------------------------------
                with btn_cols[2]:
                    with st.popover("✏️"):
                        task_live = tasks_repo.get_task(tid) or t
                        st.markdown(f"### Edit Task")
                        ntitle = st.text_input("Title", value=task_live.get('title',''), key=f"pop-title-{tid}")
                        ndesc = st.text_area("Description", value=task_live.get('description',''), key=f"pop-desc-{tid}")
                        nassignee = st.selectbox("Assignee", ["(none)"]+st.session_state.users, index=0 if not task_live.get('assignee') else (st.session_state.users.index(task_live.get('assignee'))+1 if task_live.get('assignee') in st.session_state.users else 0), key=f"pop-assignee-{tid}")
                        # Priority edited via board arrow buttons (read-only display here)
                        st.text_input("Priority", value=task_live.get('priority'), disabled=True, key=f"pop-priority-ro-{tid}")
                        ndue = st.date_input("Due", value=pd.to_datetime(task_live.get('due_date')).date() if task_live.get('due_date') else date.today(), key=f"pop-due-{tid}")
                        nest = st.number_input("Est. hours", min_value=0.0, step=0.5, value=float(task_live.get('estimates_hours') or 0.0), key=f"pop-est-{tid}")
                        ntags = st.text_input("Tags (comma)", value=", ".join(task_live.get('tags') or []), key=f"pop-tags-{tid}")
                        # Status editing disabled in popover (read-only display)
                        st.text_input("Status", value=task_live.get('status'), key=f"pop-status-ro-{tid}", disabled=True)
                        reviewer_choices = ["(none)"] + st.session_state.users
                        reviewer_current = task_live.get('reviewer') or "(none)"
                        reviewer_sel = st.selectbox("Reviewer", reviewer_choices, index=reviewer_choices.index(reviewer_current) if reviewer_current in reviewer_choices else 0, key=f"pop-rev-{tid}")
                        st.caption(f"Reporter: {task_live.get('reporter') or '(unknown)'}")
                        # Simple comments + checklist inside popover
                        st.markdown("**Checklist**")
                        cl_items = (task_live.get('checklist') or [])[-8:]
                        for ci in cl_items:
                            cid = ci.get('id')
                            cols_ci = st.columns([0.1,0.75,0.15])
                            with cols_ci[0]:
                                chk = st.checkbox("", value=ci.get('done', False), key=f"pop-cl-{cid}")
                            with cols_ci[1]:
                                st.caption(("~~"+ci.get('text','')+"~~") if chk else ci.get('text',''))
                            with cols_ci[2]:
                                if st.button("🗑", key=f"pop-delcl-{cid}"):
                                    tasks_repo.delete_check_item(tid, cid, by=st.session_state.username)
                                    st.session_state.tasks_cache = load_tasks()
                                    st.rerun()
                            if chk != ci.get('done'):
                                tasks_repo.toggle_check_item(tid, cid, chk, by=st.session_state.username)
                                st.session_state.tasks_cache = load_tasks()
                                st.rerun()
                        new_ci = st.text_input("Add checklist item", key=f"pop-new-ci-{tid}")
                        if st.button("Add Item", key=f"pop-add-ci-{tid}") and new_ci.strip():
                            tasks_repo.add_check_item(tid, new_ci.strip(), by=st.session_state.username)
                            st.session_state.tasks_cache = load_tasks()
                            st.rerun()
                        st.markdown("**Quick Comment**")
                        new_cc = st.text_input("Comment", key=f"pop-new-comment-{tid}")
                        if st.button("Post", key=f"pop-post-comment-{tid}") and new_cc.strip():
                            tasks_repo.add_comment(tid, new_cc.strip(), by=st.session_state.username)
                            st.session_state.tasks_cache = load_tasks()
                            st.rerun()
                        if st.button("💾 Save", key=f"pop-save-{tid}"):
                            history = (tasks_repo.get_task(tid) or {}).get('history', [])
                            history.append({"when": datetime.utcnow().isoformat(), "what": "edited", "by": st.session_state.username})
                            reviewer_final = None if reviewer_sel == "(none)" else reviewer_sel
                            tasks_repo.update_task({
                                'id': tid,
                                'title': ntitle,
                                'description': ndesc,
                                'assignee': None if nassignee == "(none)" else nassignee,
                                'priority': task_live.get('priority'),
                                'due_date': ndue.isoformat(),
                                'estimates_hours': nest,
                                'tags': [x.strip() for x in ntags.split(',') if x.strip()],
                                'status': task_live.get('status'),
                                'history': history,
                                'reporter': task_live.get('reporter'),
                                'reviewer': reviewer_final,
                            })
                            st.session_state.tasks_cache = load_tasks()
                            st.success("Saved")
                            st.rerun()
                # Priority lower -----------------------------------------------------------
                with btn_cols[3]:
                    cur_p = t.get('priority')
                    if cur_p in PRIORITIES:
                        pi = PRIORITIES.index(cur_p)
                        if pi > 0:
                            if st.button("↓", help="Lower priority", key=f"prio-down-{tid}"):
                                new_p = PRIORITIES[pi-1]
                                live = tasks_repo.get_task(tid) or t
                                hist = live.get('history', [])
                                hist.append({"when": datetime.utcnow().isoformat(), "what": f"priority->{new_p}", "by": st.session_state.username})
                                tasks_repo.update_task({'id': tid, 'priority': new_p, 'history': hist})
                                st.session_state.tasks_cache = load_tasks()
                                st.rerun()
                        else:
                            st.markdown("<div style='text-align:center;opacity:.35;'>—</div>", unsafe_allow_html=True)
                # Next button --------------------------------------------------------------
                with btn_cols[4]:
                    can_show_next = idx < len(statuses)-1
                    if t.get('status') == 'In Progress':
                        cl = t.get('checklist') or []
                        if cl and not all(ci.get('done') for ci in cl):
                            can_show_next = False
                    if can_show_next:
                        next_status = statuses[idx+1]
                        if next_status not in ('Deferred','Closed'):
                            if st.button("→", key=f"next-{tid}"):
                                tasks_repo.update_task_status(tid, next_status, by=st.session_state.username)
                                st.session_state.tasks_cache = load_tasks()
                                st.rerun()
                # Legacy inline edit removed (popover now handles editing); deletion disabled
            st.markdown('</div>', unsafe_allow_html=True)

with analytics_tab:
    st.subheader("Analytics (Filtered View) ✨")
    if df.empty:
        st.info("No tasks available for analytics (after filters).")
    else:
        # Determine dynamic flow statuses (base flow always) + optionally Closed if visible
        base_flow = FLOW_STATUSES.copy()
        dynamic_flow = base_flow + (["Closed"] if st.session_state.get('show_closed') else [])
        # Optionally include Deferred in charts only if visible
        include_deferred = st.session_state.get('show_deferred')
        if include_deferred:
            dynamic_flow.append(DEFERRED_STATUS)
        status_colors = {"Backlog": "#636e72","To Do": "#0984e3","In Progress": "#fdcb6e","Review": "#6c5ce7","Done": "#00b894","Closed":"#b2bec3","Deferred":"#485460"}
        priority_colors = {"Low": "#55efc4","Medium": "#74b9ff","High": "#e17055","Critical": "#d63031"}
        # Filter out Deferred if not visible
        analytic_df = df.copy()
        if not include_deferred:
            analytic_df = analytic_df[analytic_df.status != DEFERRED_STATUS]
        # Status distribution vs priority
        status_priority = analytic_df.groupby(['status','priority']).size().reset_index(name='count')
        pivot = status_priority.pivot(index='status', columns='priority', values='count').reindex(dynamic_flow).fillna(0)
        fig_stack_email = go.Figure()
        for p in PRIORITIES:
            if p in pivot.columns:
                fig_stack_email.add_bar(x=pivot.index, y=pivot[p], name=p, marker_color=priority_colors.get(p,'#999'))
        fig_stack_email.update_layout(barmode='stack', template='plotly_white', margin=dict(l=6,r=6,t=30,b=10), height=300, legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1))
        # Assignee workload
        aw_df = analytic_df.copy(); aw_df['assignee'] = aw_df['assignee'].fillna('Unassigned')
        aw = aw_df.groupby(['assignee','status']).size().reset_index(name='count')
        assignees_order = aw.groupby('assignee')['count'].sum().sort_values(ascending=False).index.tolist()
        pivot_aw = aw.pivot(index='assignee', columns='status', values='count').reindex(assignees_order).fillna(0)
        fig_aw_email = go.Figure()
        for st_status in dynamic_flow:
            if st_status in pivot_aw.columns:
                fig_aw_email.add_bar(y=pivot_aw.index, x=pivot_aw[st_status], name=st_status, orientation='h', marker_color=status_colors.get(st_status,'#888'))
        fig_aw_email.update_layout(barmode='stack', template='plotly_white', margin=dict(l=6,r=6,t=30,b=10), height=360, showlegend=True, legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1))
        # Heatmap uses only core flow + Closed (no Deferred row) for consistency
        heat_flow = base_flow + (["Closed"] if st.session_state.get('show_closed') else [])
        heat_df = analytic_df[analytic_df.status.isin(heat_flow)].copy()
        heat_counts = heat_df.groupby(['priority','status']).size().reset_index(name='count')
        matrix = []
        for p in PRIORITIES:
            row = []
            for s in heat_flow:
                val = heat_counts[(heat_counts.priority==p) & (heat_counts.status==s)]['count']
                row.append(int(val.iloc[0]) if not val.empty else 0)
            matrix.append(row)
        fig_heat_email = go.Figure(data=go.Heatmap(z=matrix, x=heat_flow, y=PRIORITIES, colorscale='Blues', showscale=True))
        fig_heat_email.update_layout(margin=dict(l=6,r=6,t=30,b=10), height=360, template='plotly_white')
        c1, c2, c3 = st.columns([1,1,1])
        with c1: st.plotly_chart(fig_stack_email, use_container_width=True)
        with c2: st.plotly_chart(fig_aw_email, use_container_width=True)
        with c3: st.plotly_chart(fig_heat_email, use_container_width=True)

with report_tab:
    st.subheader("Email / Report Preview")
    if df.empty:
        st.info("No tasks to summarize (current view scope).")
    else:
        import base64
        from io import BytesIO
        # Dynamic flows (include Closed if visible, exclude Deferred unless visible for open list context but still not in completion KPI if not shown)
        base_flow = FLOW_STATUSES.copy()
        flow_for_charts = base_flow + (["Closed"] if st.session_state.get('show_closed') else [])
        include_deferred = st.session_state.get('show_deferred')
        report_df = df.copy()
        if not include_deferred:
            report_df = report_df[report_df.status != DEFERRED_STATUS]
        # Charts (reuse same logic as analytics for consistency)
        status_priority = report_df.groupby(['status','priority']).size().reset_index(name='count')
        pivot = status_priority.pivot(index='status', columns='priority', values='count').reindex(flow_for_charts).fillna(0)
        priority_colors = {"Low": "#55efc4","Medium": "#74b9ff","High": "#e17055","Critical": "#d63031"}
        fig_stack_email = go.Figure()
        for p in PRIORITIES:
            if p in pivot.columns:
                fig_stack_email.add_bar(x=pivot.index, y=pivot[p], name=p, marker_color=priority_colors.get(p,'#999'))
        fig_stack_email.update_layout(barmode='stack', template='plotly_white', margin=dict(l=10,r=10,t=30,b=10), showlegend=True, height=320)
        # Assignee workload
        assignee_df = report_df.copy(); assignee_df['assignee'] = assignee_df['assignee'].fillna('Unassigned')
        aw = assignee_df.groupby(['assignee','status']).size().reset_index(name='count')
        assignees_order = aw.groupby('assignee')['count'].sum().sort_values(ascending=False).index.tolist()
        pivot_aw = aw.pivot(index='assignee', columns='status', values='count').reindex(assignees_order).fillna(0)
        status_colors = {"Backlog": "#636e72","To Do": "#0984e3","In Progress": "#fdcb6e","Review": "#6c5ce7","Done": "#00b894","Closed":"#b2bec3"}
        fig_aw_email = go.Figure()
        for st_status in flow_for_charts:
            if st_status in pivot_aw.columns:
                fig_aw_email.add_bar(y=pivot_aw.index, x=pivot_aw[st_status], name=st_status, orientation='h', marker_color=status_colors.get(st_status,'#888'))
        fig_aw_email.update_layout(barmode='stack', template='plotly_white', margin=dict(l=10,r=10,t=30,b=10), height=420, showlegend=True)
        # Heatmap (no Deferred row; may include Closed)
        heat = report_df[report_df.status.isin(flow_for_charts)].groupby(['priority','status']).size().reset_index(name='count')
        matrix = []
        for p in PRIORITIES:
            row = []
            for s in flow_for_charts:
                val = heat[(heat.priority==p) & (heat.status==s)]['count']
                row.append(int(val.iloc[0]) if not val.empty else 0)
            matrix.append(row)
        fig_heat_email = go.Figure(data=go.Heatmap(z=matrix, x=flow_for_charts, y=PRIORITIES, colorscale='Blues', showscale=True))
        fig_heat_email.update_layout(margin=dict(l=10,r=10,t=30,b=10), height=420, template='plotly_white')
        def fig_to_base64(fig):
            try:
                img_bytes = fig.to_image(format="png", scale=2)
                return base64.b64encode(img_bytes).decode('utf-8')
            except Exception:
                return None
        b64_stack = fig_to_base64(fig_stack_email)
        b64_aw = fig_to_base64(fig_aw_email)
        b64_heat = fig_to_base64(fig_heat_email)
        # KPIs based on report_df (which already excludes Deferred if hidden)
        total_tasks = len(report_df)
        open_tasks = len([t for t in filtered if t.get('status') not in ('Done','Closed')])  # open ignoring Closed/Done
        overdue = len([t for t in report_df.to_dict('records') if t.get('due_date') and pd.to_datetime(t.get('due_date')) < pd.Timestamp(date.today()) and t.get('status') not in ('Done','Closed')])
        done_count = len([t for t in report_df.to_dict('records') if t.get('status') == 'Done'])
        completion_pct = round(done_count/total_tasks*100,1) if total_tasks else 0
        critical_open = len([t for t in report_df.to_dict('records') if t.get('priority')=='Critical' and t.get('status') not in ('Done','Closed')])
        report_actor = None
        if st.session_state.get('my_view'):
            report_actor = st.session_state.get('current_user') or None
        elif assignee_filter and assignee_filter != 'All':
            report_actor = assignee_filter
        title_text = f"{report_actor} Task Summary" if report_actor else "Team Task Executive Summary"
        email_parts = []
        email_parts.append('<div style="font-family:Segoe UI,Arial,sans-serif;max-width:900px;margin:0 auto;background:#ffffff;border:1px solid #e5edf5;border-radius:14px;padding:32px;">')
        email_parts.append(f'<h1 style="margin:0 0 4px 0;font-size:26px;color:#0b2140;">{title_text}</h1>')
        generated_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        email_parts.append(f'<div style="color:#51658a;font-size:13px;margin-bottom:22px;">Generated {generated_ts}</div>')
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
        def img_tag(b64, alt):
            return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="width:100%;border:1px solid #e1e8f0;border-radius:12px;margin-bottom:18px;" />' if b64 else f'<div style="font-size:12px;color:#d63031;margin-bottom:18px;">[Missing {alt}]</div>'
        def chart_cell(b64, alt):
            return (
                f'<div style="flex:1;min-width:260px;display:flex;flex-direction:column;">'
                f'<div style="font-size:12px;font-weight:600;color:#35506b;margin:0 0 4px 2px;letter-spacing:.5px;text-transform:uppercase;">{alt}</div>'
                f'{img_tag(b64, alt)}'
                f'</div>'
            )
        email_parts.append('<h2 style="font-size:20px;margin:0 0 12px 0;color:#0b2140;">Key Visuals</h2>')
        email_parts.append('<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px;">')
        email_parts.append(chart_cell(b64_stack, 'Status Distribution'))
        email_parts.append(chart_cell(b64_aw, 'Assignee Workload'))
        email_parts.append(chart_cell(b64_heat, 'Priority vs Status'))
        email_parts.append('</div>')
        # Critical list based on report_df
        records = report_df.to_dict('records')
        crit_list = [t for t in records if t.get('priority')=='Critical' and t.get('status') not in ('Done','Closed')]
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
        # Open tasks list
        open_tasks_raw = [t for t in records if t.get('status') not in ('Done','Closed')]
        priority_rank = {'Critical':0,'High':1,'Medium':2,'Low':3}
        def sort_key(t):
            pr = priority_rank.get(t.get('priority'), 99)
            due_raw = t.get('due_date')
            try:
                due_dt = pd.to_datetime(due_raw).to_pydatetime().date() if due_raw else None
            except Exception:
                due_dt = None
            return (pr, due_dt or date.max, t.get('title') or '')
        open_tasks_list = sorted(open_tasks_raw, key=sort_key)[:50]
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
            st.text_input("Status", value=task.get('status'), key=f"status-ro-{tid}", disabled=True)
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
                    # status unchanged (editing disabled here)
                    'status': task.get('status'),
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

with doc_tab:
        st.subheader("Documentation & Usage Guide ✨")
        st.markdown("""
        <style>
        .ttm-doc h3 {margin-top:1.4rem;margin-bottom:0.4rem;color:#0b2140;}
        .ttm-doc p {margin:0.25rem 0 0.6rem 0;line-height:1.35em;color:#34495e;}
        .ttm-doc code {background:#f3f7fb;padding:2px 6px;border-radius:6px;font-size:0.8rem;}
        .ttm-badge {display:inline-block;background:linear-gradient(120deg,#0b63d6,#6c5ce7,#00b894);color:#fff;padding:2px 9px;border-radius:14px;font-size:0.65rem;font-weight:600;letter-spacing:.5px;margin-right:6px;}
        .ttm-flow {display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 12px 0;}
        .ttm-flow span {background:#eef4fa;padding:6px 12px;border-radius:12px;font-size:0.75rem;font-weight:600;color:#35506b;position:relative;}
        .ttm-flow span:after {content:'→';position:absolute;right:-10px;top:50%;transform:translateY(-50%);font-size:0.75rem;color:#6c5ce7;}
        .ttm-flow span:last-child:after {display:none;}
        .ttm-callout {background:linear-gradient(145deg,#ffffff,#f2f7fb);border:1px solid #d0dce8;padding:12px 14px;border-radius:14px;font-size:0.75rem;color:#274056;box-shadow:0 4px 14px -6px rgba(11,99,214,0.18);}    
        .ttm-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;margin-top:10px;}
        .ttm-card {background:#fff;border:1px solid #e1e8f0;border-radius:14px;padding:14px 16px;box-shadow:0 4px 18px -6px rgba(11,99,214,0.15);}
        .ttm-card h4 {margin:0 0 6px 0;font-size:0.9rem;color:#0b2140;letter-spacing:.5px;}
        .ttm-card ul {margin:0 0 0.2rem 1.0rem;padding:0;font-size:0.72rem;}
        .ttm-card li {margin:0 0 4px 0;}
        .ttm-sect {margin-top:1.2rem;}
        </style>
        <div class='ttm-doc'>
        <p>This tab explains how the Team Task Manager works: statuses, automation, priority rules, filters, analytics, reporting, and data handling. Use it as the single source of truth for end‑users and maintainers.</p>
        <h3>1. Workflow & Status Columns</h3>
        <div class='ttm-flow'>
            <span>Backlog</span><span>To Do</span><span>In Progress</span><span>Review</span><span>Done</span><span style='background:#dfe6e9;'>Closed</span>
        </div>
        <ul style='font-size:0.75rem;margin-top:-4px;'>
            <li><code>Closed</code> is an archival lane (hidden by default). Tasks auto‑move from <code>Done</code> to <code>Closed</code> after 2 full days.</li>
            <li><code>Deferred</code> (optional column) is out‑of‑flow: excluded from KPIs, analytics, and report metrics.</li>
            <li>Navigation uses <strong>← / →</strong> buttons; tasks never move into <code>Deferred</code> or <code>Closed</code> via these arrows (automation or user toggle manages archival).</li>
        </ul>
        <h3>2. Automation & Timing</h3>
        <div class='ttm-card'>
            <h4>Auto‑Archival</h4>
            <ul>
                <li>When a task first reaches <code>Done</code>, its <code>done_at</code> timestamp is stored.</li>
                <li>Each render (if the Closed column is visible), tasks with <code>status == Done</code> and <code>now - done_at &gt; 48h</code> migrate to <code>Closed</code>.</li>
                <li>If <code>done_at</code> was missing (legacy tasks), history is scanned to infer completion time.</li>
            </ul>
        </div>
        <h3>3. Priority Model</h3>
        <p>Four ordered priorities: <code>Low &lt; Medium &lt; High &lt; Critical</code>. Adjust using ↑ / ↓ buttons on each card. Priority edits append a history entry (e.g. <code>priority-&gt;High</code>). Highest/lowest states disable the respective arrow.</p>
        <h3>4. Checklist Gating</h3>
        <p>If a task in <code>In Progress</code> has checklist items, it cannot advance to <code>Review</code> (→ button hidden) until ALL items are checked. Checklist interactions (add / toggle / delete) generate history events.</p>
        <h3>5. Task Creation & Editing Rules</h3>
        <div class='ttm-grid'>
            <div class='ttm-card'>
                <h4>Creation</h4>
                <ul>
                    <li>Inline ➕ popover in each column sets initial status.</li>
                    <li>Reporter auto‑filled as current (impersonated) user.</li>
                    <li>Optional reviewer & tags (comma separated).</li>
                </ul>
            </div>
            <div class='ttm-card'>
                <h4>Editing</h4>
                <ul>
                    <li>Status read‑only inside popover (use arrows for movement).</li>
                    <li>Priority read‑only in popover; adjust via board arrows.</li>
                    <li>Checklist + quick comments directly inline within popover.</li>
                    <li>Every save appends <code>edited</code> to history.</li>
                </ul>
            </div>
            <div class='ttm-card'>
                <h4>Detail Inspector</h4>
                <ul>
                    <li>Shows full history (latest first subset), comments, checklist.</li>
                    <li>Allows priority & assignee change (status still read‑only).</li>
                </ul>
            </div>
        </div>
        <h3 class='ttm-sect'>6. Filters & View Modes</h3>
        <ul style='font-size:0.75rem;'>
            <li><strong>My View</strong>: Limits scope to tasks where you are assignee.</li>
            <li><strong>Impersonation</strong>: Change the active user context to act as another teammate (affects Reporter on new tasks & history author).</li>
            <li>Search scans title, description, and tags (case‑insensitive).</li>
            <li>Assignee & Priority dropdowns further narrow the scope.</li>
        </ul>
        <h3>7. Card Layout</h3>
        <p>Uniform height via always‑visible checklist bar. Metadata line shows Reporter, Assignee, Created date, Due date (overdue highlighted), Reviewer, and estimated hours. Priority badge uses animated gradient per level.</p>
        <h3>8. Analytics Logic</h3>
        <ul style='font-size:0.75rem;'>
            <li>Three visuals: Stacked Status vs Priority, Assignee Workload (horizontal stacked), Priority × Status heatmap.</li>
            <li>Input dataset respects current <em>My View</em>, impersonation, and filters.</li>
            <li><code>Deferred</code> (and <code>Closed</code> unless explicitly visible in board) are excluded from KPI calculations.</li>
        </ul>
        <h3>9. Report Generation</h3>
        <p>Email preview mirrors analytics set. Charts exported to base64 PNG (requires <code>kaleido</code> for image generation). KPIs: Total, Open, Overdue, Critical Open, Completion %. Open tasks table (top 50) sorted by priority → due → title. Critical open list highlights overdue items.</p>
        <h3>10. Import / Export</h3>
        <ul style='font-size:0.75rem;'>
            <li>JSON Export preserves full objects including comments, checklist, history.</li>
            <li>JSON Import can optionally wipe existing tasks; expects list with unique <code>id</code>.</li>
            <li>CSV Export flattens fields (omits nested collections).</li>
        </ul>
        <h3>11. Sample Data Generator</h3>
        <p>Creates 30 tasks with randomized attributes, optional backdated <code>Done</code> statuses to demonstrate auto‑archival. Some tasks receive randomized checklist templates.</p>
        <h3>12. History & Audit Trail</h3>
        <p>Every significant mutation appends a history event (<code>created</code>, <code>status-&gt;X</code>, <code>priority-&gt;Y</code>, <code>edited</code>, <code>check_added</code>, <code>check_done</code>, <code>comment_added</code>, etc.). Timestamps are UTC ISO‑8601.</p>
        <h3>13. Data Persistence</h3>
        <p>SQLite database at <code>data/tasks.db</code> via SQLAlchemy. JSON‑encoded list fields keep schema minimal. Lightweight in‑place migrations add missing columns if needed. To scale, replace the DB URL with PostgreSQL and introduce normalized tables for history/comments.</p>
        <h3>14. Limitations & Future Enhancements</h3>
        <ul style='font-size:0.75rem;'>
            <li>No authentication layer (impersonation is trust‑based).</li>
            <li>No drag‑and‑drop reordering (priority / movement via buttons).</li>
            <li>No SLA/time‑in‑status analytics yet (could leverage <code>history</code> timestamps).</li>
            <li>Attachments / rich text not supported.</li>
        </ul>
        <div class='ttm-callout'><strong>Tip:</strong> Toggle <code>Closed</code> to watch auto‑archival in action after seeding sample tasks with backdated completions.</div>
        </div>
        """, unsafe_allow_html=True)

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
