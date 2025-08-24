
import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime, date
import uuid
import io
import plotly.express as px

# --- Custom CSS for modern, professional look ---
st.markdown(
    """
    <style>
    .ttm-container { max-width: 1200px; margin: 0 auto; }
    .ttm-kanban-col {
        background: #f7fafd;
        border-radius: 14px;
        box-shadow: 0 2px 12px rgba(11,99,214,0.06);
        padding: 1.1rem 0.7rem 0.7rem 0.7rem;
        margin-bottom: 1.2rem;
        min-height: 180px;
    }
    .ttm-status-header {
        font-size: 1.1rem;
        font-weight: 700;
        letter-spacing: 0.5px;
        margin-bottom: 0.7rem;
        padding: 0.3rem 0;
        border-radius: 8px;
        color: #fff;
        text-align: center;
    }
    .ttm-status-Backlog { background: linear-gradient(90deg,#b2bec3,#636e72); }
    .ttm-status-To-Do { background: linear-gradient(90deg,#74b9ff,#0984e3); }
    .ttm-status-In-Progress { background: linear-gradient(90deg,#ffeaa7,#fdcb6e); color:#222; }
    .ttm-status-Review { background: linear-gradient(90deg,#a29bfe,#6c5ce7); }
    .ttm-status-Done { background: linear-gradient(90deg,#55efc4,#00b894); color:#222; }
    .ttm-task-card {
        background: #fff;
        border-radius: 10px;
        box-shadow: 0 1px 6px rgba(11,99,214,0.07);
        padding: 0.8rem 0.7rem 0.7rem 0.7rem;
        margin-bottom: 0.8rem;
        transition: box-shadow 0.2s;
        border-left: 6px solid #0b63d6;
    }
    .ttm-task-card:hover { box-shadow: 0 4px 18px rgba(11,99,214,0.13); }
    .ttm-task-title { font-size: 1.08rem; font-weight: 600; color: #0b2140; margin-bottom: 0.2rem; }
    .ttm-task-meta { color: #6b7b8f; font-size: 0.97rem; margin-bottom: 0.2rem; }
    .ttm-task-tags { font-size: 0.92rem; color: #0984e3; margin-bottom: 0.2rem; }
    .ttm-priority-badge {
        display: inline-block;
        font-size: 0.93rem;
        font-weight: 700;
        border-radius: 7px;
        padding: 0.13rem 0.7rem;
        margin-left: 0.3rem;
        margin-bottom: 0.1rem;
        color: #fff;
        letter-spacing: 0.5px;
        vertical-align: middle;
    }
    .ttm-priority-Low { background: linear-gradient(90deg,#00b894,#55efc4); color: #fff; }
    .ttm-priority-Medium { background: linear-gradient(90deg,#0984e3,#74b9ff); color: #fff; }
    .ttm-priority-High { background: linear-gradient(90deg,#fdcb6e,#e17055); color: #222; }
    .ttm-priority-Critical { background: linear-gradient(90deg,#d63031,#b71c1c); color: #fff; border: 2px solid #b71c1c; box-shadow: 0 0 0 2px #fff, 0 0 8px 2px #d63031; }
    .ttm-btn-row { display: flex; gap: 0.5rem; margin-top: 0.3rem; }
    /* Style all Streamlit buttons in Kanban columns */
    div[data-testid="column"] button {
        background: linear-gradient(90deg,#0b63d6,#0984e3);
        color: #fff;
        border: none;
        border-radius: 7px;
        padding: 0.32rem 1.1rem;
        font-size: 0.98rem;
        font-weight: 600;
        cursor: pointer;
        transition: background 0.2s;
        box-shadow: 0 1px 4px rgba(11,99,214,0.08);
        margin-bottom: 0.1rem;
    }
    div[data-testid="column"] button:hover {
        background: linear-gradient(90deg,#0056b3,#0b63d6);
    }
    /* Priority-based button coloring */
    div[data-testid="column"] button[data-priority="Critical"] {
        background: linear-gradient(90deg,#d63031,#b71c1c);
        color: #fff;
        border: 2px solid #b71c1c;
        box-shadow: 0 0 0 2px #fff, 0 0 8px 2px #d63031;
    }
    div[data-testid="column"] button[data-priority="High"] {
        background: linear-gradient(90deg,#fdcb6e,#e17055);
        color: #222;
    }
    div[data-testid="column"] button[data-priority="Medium"] {
        background: linear-gradient(90deg,#0984e3,#74b9ff);
        color: #fff;
    }
    div[data-testid="column"] button[data-priority="Low"] {
        background: linear-gradient(90deg,#00b894,#55efc4);
        color: #fff;
    }
    .ttm-detail {
        background: #f7fafd;
        border-radius: 12px;
        box-shadow: 0 2px 12px rgba(11,99,214,0.06);
        padding: 1.2rem 1.2rem 0.7rem 1.2rem;
        margin-bottom: 1.2rem;
    }
    .ttm-detail-title { font-size: 1.3rem; font-weight: 700; color: #0b63d6; margin-bottom: 0.5rem; }
    .ttm-detail-label { color: #51658a; font-size: 1.01rem; font-weight: 600; margin-top: 0.7rem; }
    .ttm-detail-value { color: #222; font-size: 1.01rem; margin-bottom: 0.2rem; }
    .ttm-detail-history, .ttm-detail-comments { font-size: 0.97rem; color: #6b7b8f; margin-bottom: 0.2rem; }
    .ttm-detail-comment-box { margin-top: 0.7rem; }
    .ttm-kpi-box { background: #fff; border-radius: 10px; box-shadow: 0 1px 6px rgba(11,99,214,0.07); padding: 1.1rem 0.7rem; text-align:center; }
    .ttm-kpi-label { color: #51658a; font-size: 1.01rem; }
    .ttm-kpi-value { font-size: 1.5rem; font-weight: 700; color: #0b63d6; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Page config
st.set_page_config(page_title="Team Task Manager", page_icon="📋", layout="wide")

# Storage path (persistent local JSON)
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.json")

# Ensure data dir exists
os.makedirs(DATA_DIR, exist_ok=True)

# ----- Utilities -----
STATUS_ORDER = ["Backlog", "To Do", "In Progress", "Review", "Done"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]


def load_tasks():
    if not os.path.exists(TASKS_FILE):
        return []
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_tasks(tasks):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False, default=str)


def new_task_dict(title, description, assignee, priority, due_date, estimates, tags):
    return {
        "id": str(uuid.uuid4()),
        "title": title.strip(),
        "description": description.strip(),
        "assignee": assignee or "Unassigned",
        "priority": priority,
        "status": "Backlog",
        "created_at": datetime.utcnow().isoformat(),
        "due_date": due_date.isoformat() if isinstance(due_date, (date, datetime)) else None,
        "estimates_hours": estimates,
        "tags": tags,
        "comments": [],
        "history": [
            {"when": datetime.utcnow().isoformat(), "what": "created", "by": assignee or "system"}
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
if "tasks" not in st.session_state:
    st.session_state.tasks = load_tasks()

if "users" not in st.session_state:
    # initial sample users; user can edit
    st.session_state.users = ["Alice", "Bob", "Carol", "Dave"]

# Add a small sample dataset if empty
if not st.session_state.tasks:
    sample = [
        new_task_dict("Onboard new hire", "Prepare environment and docs", "Alice", "High", date.today(), 4, ["onboarding"]),
        new_task_dict("Q3 Roadmap", "Finalize objectives", "Bob", "Medium", date.today(), 8, ["planning"]),
        new_task_dict("Bug #432: login error", "Intermittent login failures in auth module", "Carol", "Critical", date.today(), 6, ["bug"]),
    ]
    st.session_state.tasks = sample
    save_tasks(st.session_state.tasks)


# ----- Layout -----

# --- Main container ---
st.markdown('<div class="ttm-container">', unsafe_allow_html=True)
st.title("Team Task Manager — Ultimate")
st.markdown("<span style='color:#51658a;font-size:1.1rem;'>Professional task management built into your Streamlit site.</span>", unsafe_allow_html=True)

# Top-level controls
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    search = st.text_input("Search tasks (title, description, tags)")
with c2:
    assignee_filter = st.selectbox("Assignee", options=["All"] + st.session_state.users, index=0)
with c3:
    priority_filter = st.selectbox("Priority", options=["All"] + PRIORITIES, index=0)

# Create task panel
with st.expander("Create new task", expanded=False):
    tcol1, tcol2 = st.columns(2)
    with tcol1:
        title = st.text_input("Title")
        assignee = st.selectbox("Assignee", options=["(none)"] + st.session_state.users, index=0)
        priority = st.selectbox("Priority", PRIORITIES, index=1)
        estimates = st.number_input("Estimates (hours)", min_value=0.0, value=1.0)
    with tcol2:
        description = st.text_area("Description")
    due = st.date_input("Due date", value=date.today(), key="create-due-date")
    tags_in = st.text_input("Tags (comma separated)")

    create_btn = st.button("Create Task")
    if create_btn:
        assignee_val = None if assignee == "(none)" else assignee
        tags = [t.strip() for t in tags_in.split(",") if t.strip()]
        t = new_task_dict(title, description, assignee_val, priority, due, estimates, tags)
        st.session_state.tasks.insert(0, t)
        save_tasks(st.session_state.tasks)
        st.success("Task created")
        st.rerun()


# Management: quick user add
with st.sidebar.expander("Team & Settings", expanded=False):
    st.subheader("Team members")
    new_user = st.text_input("Add team member")
    if st.button("Add member") and new_user.strip():
        st.session_state.users.append(new_user.strip())
        st.success("Member added")
    st.write(st.session_state.users)
    st.markdown("---")
    if st.button("Save tasks now"):
        save_tasks(st.session_state.tasks)
        st.success("Saved")

# Filter tasks
filtered = st.session_state.tasks
if search:
    s = search.lower()
    filtered = [t for t in filtered if s in t.get("title","").lower() or s in t.get("description"," ").lower() or any(s in tag.lower() for tag in (t.get("tags") or []))]
if assignee_filter and assignee_filter != "All":
    filtered = [t for t in filtered if (t.get("assignee") or "Unassigned") == assignee_filter]
if priority_filter and priority_filter != "All":
    filtered = [t for t in filtered if t.get("priority") == priority_filter]

# Convert to DataFrame for visualizations
df = tasks_to_df(st.session_state.tasks)


# KPIs (modern look)
k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown('<div class="ttm-kpi-box">', unsafe_allow_html=True)
    st.markdown('<div class="ttm-kpi-label">Total Tasks</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ttm-kpi-value">{len(st.session_state.tasks)}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
with k2:
    open_tasks = len([t for t in st.session_state.tasks if t.get("status") != "Done"])
    st.markdown('<div class="ttm-kpi-box">', unsafe_allow_html=True)
    st.markdown('<div class="ttm-kpi-label">Open Tasks</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ttm-kpi-value">{open_tasks}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
with k3:
    overdue = len([t for t in st.session_state.tasks if t.get("due_date") and pd.to_datetime(t.get("due_date")) < pd.Timestamp(date.today()) and t.get("status") != "Done"])
    st.markdown('<div class="ttm-kpi-box">', unsafe_allow_html=True)
    st.markdown('<div class="ttm-kpi-label">Overdue</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ttm-kpi-value">{overdue}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
with k4:
    completed_pct = 0
    if st.session_state.tasks:
        completed_pct = round(len([t for t in st.session_state.tasks if t.get("status") == "Done"]) / len(st.session_state.tasks) * 100, 1)
    st.markdown('<div class="ttm-kpi-box">', unsafe_allow_html=True)
    st.markdown('<div class="ttm-kpi-label">Completed %</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ttm-kpi-value">{completed_pct}%</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("---")


# Kanban style board (modern look)
st.subheader("Kanban Board")
cols = st.columns(len(STATUS_ORDER))
for idx, status in enumerate(STATUS_ORDER):
    with cols[idx]:
        # Precompute class name for status header (replace space with hyphen)
        status_class = f"ttm-status-{status.replace(' ', '-') }"
        st.markdown(f'<div class="ttm-status-header {status_class}">{status}</div>', unsafe_allow_html=True)
        st.markdown('<div class="ttm-kanban-col">', unsafe_allow_html=True)
        status_tasks = [t for t in filtered if t.get("status") == status]
        for t in status_tasks:
            st.markdown(f'<div class="ttm-task-card">', unsafe_allow_html=True)
            # Priority badge
            priority = t.get("priority", "Medium")
            badge_html = f'<span class="ttm-priority-badge ttm-priority-{priority}">{priority}</span>'
            st.markdown(f'<div class="ttm-task-title">{t.get("title")} {badge_html}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="ttm-task-meta">👤 {t.get("assignee") or "Unassigned"} &nbsp;|&nbsp; 📅 {t.get("due_date") or "—"}</div>', unsafe_allow_html=True)
            if t.get('tags'):
                st.markdown(f'<div class="ttm-task-tags">🏷️ {", ".join(t.get("tags"))}</div>', unsafe_allow_html=True)
            if t.get('estimates_hours'):
                st.markdown(f'<div class="ttm-task-meta">⏱️ {t.get("estimates_hours")}h</div>', unsafe_allow_html=True)
            # Actions row using st.columns, only inside the card
            action_cols = st.columns([1,1,1,1])
            with action_cols[0]:
                if st.button('🔍 View', key=f"view-{t['id']}", help="View details"):
                    st.session_state._inspect = t['id']
            with action_cols[1]:
                if st.button('⬅️ Prev', key=f"prev-{t['id']}", help="Move to previous status"):
                    cur_idx = STATUS_ORDER.index(t['status'])
                    if cur_idx > 0:
                        t['status'] = STATUS_ORDER[cur_idx-1]
                        t.setdefault('history',[]).append({"when":datetime.utcnow().isoformat(),"what":"moved_prev","by":"user"})
                        save_tasks(st.session_state.tasks)
                        st.rerun()
            with action_cols[2]:
                if st.button('➡️ Next', key=f"next-{t['id']}", help="Move to next status"):
                    cur_idx = STATUS_ORDER.index(t['status'])
                    if cur_idx < len(STATUS_ORDER)-1:
                        t['status'] = STATUS_ORDER[cur_idx+1]
                        t.setdefault('history',[]).append({"when":datetime.utcnow().isoformat(),"what":"moved_next","by":"user"})
                        save_tasks(st.session_state.tasks)
                        st.rerun()
            with action_cols[3]:
                if st.button('🗑️ Del', key=f"del-{t['id']}", help="Delete task"):
                    st.session_state.tasks = [x for x in st.session_state.tasks if x['id'] != t['id']]
                    save_tasks(st.session_state.tasks)
                    st.success("Deleted")
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

st.markdown("---")

# Inspector / Detail view (modern look)
if '_inspect' in st.session_state:
    tid = st.session_state._inspect
    task = next((x for x in st.session_state.tasks if x['id'] == tid), None)
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
            new_comment = st.text_area("Add comment", key=f"comment-{tid}", placeholder="Type your comment and press Enter or click Post")
            if st.button("💬 Post comment", key=f"post-{tid}"):
                if new_comment.strip():
                    task.setdefault('comments', []).insert(0, {"when": datetime.utcnow().isoformat(), "by": "You", "text": new_comment.strip()})
                    save_tasks(st.session_state.tasks)
                    st.rerun()
        with dcol2:
            st.markdown('<div class="ttm-detail-label">Status</div>', unsafe_allow_html=True)
            new_status = st.selectbox("Status", STATUS_ORDER, index=STATUS_ORDER.index(task.get('status')), key=f"status-{tid}")
            st.markdown('<div class="ttm-detail-label">Assignee</div>', unsafe_allow_html=True)
            new_assignee = st.selectbox("Assignee", options=["(none)"]+st.session_state.users, index=0 if task.get('assignee') is None else (st.session_state.users.index(task.get('assignee'))+1 if task.get('assignee') in st.session_state.users else 0), key=f"assignee-{tid}")
            st.markdown('<div class="ttm-detail-label">Priority</div>', unsafe_allow_html=True)
            new_priority = st.selectbox("Priority", PRIORITIES, index=PRIORITIES.index(task.get('priority')) if task.get('priority') in PRIORITIES else 1, key=f"priority-{tid}")
            st.markdown('<div class="ttm-detail-label">Due date</div>', unsafe_allow_html=True)
            new_due = st.date_input("Due date", value=pd.to_datetime(task.get('due_date')).date() if task.get('due_date') else date.today(), key=f"edit-due-date-{tid}")
            if st.button("💾 Save changes", key=f"save-{tid}"):
                task['status'] = new_status
                task['assignee'] = None if new_assignee == "(none)" else new_assignee
                task['priority'] = new_priority
                task['due_date'] = new_due.isoformat()
                task.setdefault('history', []).append({"when":datetime.utcnow().isoformat(),"what":"edited","by":"You"})
                save_tasks(st.session_state.tasks)
                st.success("Saved")
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

# ----- Visualizations and Reports -----
st.markdown("---")
st.subheader("Reports & Visualizations")
rv1, rv2 = st.columns([1,1])
with rv1:
    st.markdown("**Tasks by Priority**")
    if not df.empty:
        pcount = df.groupby('priority').size().reindex(PRIORITIES, fill_value=0).reset_index(name='count')
        fig = px.bar(pcount, x='priority', y='count', color='priority', color_discrete_sequence=px.colors.qualitative.Prism)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No tasks to visualize")
with rv2:
    st.markdown("**Tasks by Assignee**")
    if not df.empty:
        acount = df.groupby('assignee').size().reset_index(name='count')
        fig2 = px.bar(acount, x='assignee', y='count', color='assignee')
        st.plotly_chart(fig2, use_container_width=True)

# Simple burndown-like chart: completed tasks over time
if not df.empty:
    dft = df.copy()
    dft['created_at'] = pd.to_datetime(dft['created_at'], errors='coerce') if 'created_at' in dft.columns else pd.NaT
    dft['done_at'] = pd.to_datetime([next((h['when'] for h in t.get('history') if h.get('what')=='moved_next' and STATUS_ORDER.index(t.get('status', 'Backlog'))==len(STATUS_ORDER)-1), None) for t in st.session_state.tasks], errors='coerce')
    # fallback simple timeline of due dates vs status
    try:
        timeline = df.groupby('due_date').size().reset_index(name='count')
        timeline['due_date'] = pd.to_datetime(timeline['due_date'])
        fig3 = px.area(timeline.sort_values('due_date'), x='due_date', y='count')
        st.plotly_chart(fig3, use_container_width=True)
    except Exception:
        pass

# Export / Import
st.markdown("---")
export_col, import_col = st.columns(2)
with export_col:
    st.markdown("**Export**")
    if st.button("Download JSON"):
        b = io.BytesIO()
        b.write(json.dumps(st.session_state.tasks, indent=2, ensure_ascii=False).encode('utf-8'))
        b.seek(0)
        st.download_button("Save tasks.json", data=b, file_name="tasks.json", mime="application/json")
    if st.button("Download CSV"):
        df = tasks_to_df(st.session_state.tasks)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("Save tasks.csv", data=csv, file_name="tasks.csv", mime='text/csv')
with import_col:
    st.markdown("**Import**")
    up = st.file_uploader("Upload tasks JSON", type=["json"])
    if up is not None:
        try:
            data = json.load(up)
            if isinstance(data, list):
                st.session_state.tasks = data
                save_tasks(st.session_state.tasks)
                st.success("Imported tasks")
                st.rerun()
            else:
                st.error("Invalid format: JSON should be a list of tasks")
        except Exception as e:
            st.error(f"Failed to import: {e}")

st.markdown("---")

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
st.markdown("\n---\n</div>" )
st.caption("Ultimate Team Task Manager — concise, secure, and ready for customization")
