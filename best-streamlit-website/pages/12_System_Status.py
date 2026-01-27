import asyncio
import streamlit as st
import requests
from datetime import datetime
from pathlib import Path
import os

from src.theme import set_theme
from src.mcp_health import check_mcp_server_simple


set_theme(page_title="System Status", page_icon="🔍")


st.markdown(
    """
    <style>
    .status-hero {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 20px;
        padding: 2rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(102, 126, 234, 0.4);
    }
    .status-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        letter-spacing: 0.5px;
    }
    .status-hero p {
        margin: 0;
        font-size: 1.05rem;
        opacity: 0.95;
    }
    .status-card {
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
        margin-bottom: 1rem;
    }
    .status-card h3 {
        font-size: 1.2rem;
        font-weight: 700;
        margin: 0 0 0.5rem 0;
        color: #1e293b;
    }
    .status-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 12px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .status-healthy {
        background: #dcfce7;
        color: #166534;
        border: 1px solid #86efac;
    }
    .status-unhealthy {
        background: #fee2e2;
        color: #991b1b;
        border: 1px solid #fca5a5;
    }
    .status-unknown {
        background: #fef3c7;
        color: #92400e;
        border: 1px solid #fcd34d;
    }
    .status-info {
        background: #dbeafe;
        color: #1e40af;
        border: 1px solid #93c5fd;
    }
    .metric-row {
        display: flex;
        justify-content: space-between;
        padding: 0.5rem 0;
        border-bottom: 1px solid #f1f5f9;
    }
    .metric-row:last-child {
        border-bottom: none;
    }
    .metric-label {
        font-weight: 600;
        color: #475569;
    }
    .metric-value {
        color: #1e293b;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


st.markdown(
    """
    <div class="status-hero">
        <h1>🔍 System Status Dashboard</h1>
        <p>Real-time monitoring of all services, databases, and MCP servers</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def check_service_health(name: str, url: str, endpoint: str = "/health") -> dict:
    """Check if a service is healthy by hitting its health endpoint."""
    try:
        response = requests.get(f"{url}{endpoint}", timeout=3)
        if response.status_code == 200:
            return {
                "status": "healthy",
                "message": "Service is running",
                "details": response.json() if response.headers.get("content-type", "").startswith("application/json") else None,
            }
        else:
            return {
                "status": "unhealthy",
                "message": f"HTTP {response.status_code}",
                "details": None,
            }
    except requests.exceptions.ConnectionError:
        return {
            "status": "unreachable",
            "message": "Connection refused - service may not be running",
            "details": None,
        }
    except requests.exceptions.Timeout:
        return {
            "status": "timeout",
            "message": "Request timed out",
            "details": None,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "details": None,
        }


def check_database(db_url: str) -> dict:
    """Check if a database is accessible via SQLAlchemy."""
    if not (db_url or "").strip():
        return {
            "status": "missing",
            "message": "Database URL not set",
            "tables": 0,
        }

    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy import inspect as sa_inspect

        engine = create_engine(db_url, future=True, echo=False, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        table_count = len(sa_inspect(engine).get_table_names())
        return {
            "status": "healthy",
            "message": "Database accessible",
            "tables": table_count,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "tables": 0,
        }


def get_environment_info() -> dict:
    """Get environment and deployment information."""
    return {
        "python_version": os.popen("python --version 2>&1").read().strip(),
        "deployment_mode": os.getenv("DEPLOYMENT_MODE", "docker-compose"),
        "ollama_enabled": os.getenv("OLLAMA_ENABLED", "true"),
        "ollama_model": os.getenv("OLLAMA_MODEL", "tinyllama"),
        "database_url": os.getenv("DATABASE_URL", "postgresql+psycopg2://bsw:bsw@postgres:5432/bsw"),
        "scheduler_url": os.getenv("SCHEDULER_MCP_URL", "http://scheduler:8010"),
    }


# Auto-refresh toggle
col1, col2 = st.columns([3, 1])
with col1:
    st.subheader("Service Health Checks")
with col2:
    auto_refresh = st.checkbox("Auto-refresh (10s)", value=False)

if auto_refresh:
    st.rerun()

# Define services to check
services = [
    {
        "type": "http",
        "name": "Streamlit UI",
        "url": "http://localhost:8502",
        "endpoint": "/_stcore/health",
        "description": "Main web interface",
        "icon": "🌐",
    },
    {
        "type": "mcp",
        "id": "scheduler",
        "name": "Scheduler MCP",
        "url": os.getenv("STREAMLIT_SCHEDULER_MCP_URL", os.getenv("SCHEDULER_MCP_URL", "http://scheduler:8010")),
        "description": "Background job orchestration",
        "icon": "⏱️",
    },
    {
        "type": "mcp",
        "id": "docker",
        "name": "Docker MCP",
        "url": os.getenv("STREAMLIT_DOCKER_MCP_URL", os.getenv("DOCKER_MCP_URL", "http://docker-mcp:8000")),
        "description": "Docker container management",
        "icon": "🐳",
    },
    {
        "type": "mcp",
        "id": "jenkins",
        "name": "Jenkins MCP",
        "url": os.getenv("STREAMLIT_JENKINS_MCP_URL", os.getenv("JENKINS_MCP_URL", "http://jenkins-mcp:8000")),
        "description": "CI/CD pipeline integration",
        "icon": "🔧",
    },
    {
        "type": "mcp",
        "id": "kubernetes",
        "name": "Kubernetes MCP",
        "url": os.getenv("STREAMLIT_KUBERNETES_MCP_URL", os.getenv("KUBERNETES_MCP_URL", "http://kubernetes-mcp:8000")),
        "description": "K8s cluster management",
        "icon": "☸️",
    },
    {
        "type": "mcp",
        "id": "nexus",
        "name": "Nexus MCP",
        "url": os.getenv("STREAMLIT_NEXUS_MCP_URL", os.getenv("NEXUS_MCP_URL", "http://nexus-mcp:8000")),
        "description": "Artifact repository",
        "icon": "📦",
    },
]

# Check all services
service_results = []
for service in services:
    if service.get("type") == "mcp":
        health = asyncio.run(check_mcp_server_simple(service.get("id", service["name"]), service["url"], timeout=6.0))
    else:
        health = check_service_health(service["name"], service["url"], service.get("endpoint", "/health"))
    service_results.append({**service, **health})

# Display service health in a grid
cols = st.columns(2)
for idx, result in enumerate(service_results):
    with cols[idx % 2]:
        status_class = {
            "healthy": "status-healthy",
            "unhealthy": "status-unhealthy",
            "unreachable": "status-unhealthy",
            "timeout": "status-unknown",
            "error": "status-unhealthy",
        }.get(result["status"], "status-unknown")

        status_text = {
            "healthy": "✓ Healthy",
            "unhealthy": "✗ Unhealthy",
            "unreachable": "✗ Unreachable",
            "timeout": "⏱ Timeout",
            "error": "✗ Error",
        }.get(result["status"], "? Unknown")

        st.markdown(
            f"""
            <div class="status-card">
                <h3>{result['icon']} {result['name']}</h3>
                <p style="color: #64748b; font-size: 0.9rem; margin-bottom: 1rem;">{result['description']}</p>
                <div class="metric-row">
                    <span class="metric-label">Status:</span>
                    <span class="status-badge {status_class}">{status_text}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">URL:</span>
                    <span class="metric-value" style="font-family: monospace; font-size: 0.85rem;">{result['url']}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Message:</span>
                    <span class="metric-value" style="font-size: 0.9rem;">{result['message']}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.divider()

# Database Status
st.subheader("📊 Database Status")

databases = [
    {
        "name": "Tasks Database",
        "url": os.getenv("DATABASE_URL", "postgresql+psycopg2://bsw:bsw@postgres:5432/bsw"),
        "description": "Task management and team data",
    },
    {
        "name": "Scheduler Database",
        "url": os.getenv("PLATFORM_DATABASE_URL", os.getenv("SCHEDULER_DATABASE_URL", "postgresql+psycopg2://bsw:bsw@postgres:5432/bsw")),
        "description": "Job scheduling and execution history",
    },
]

db_cols = st.columns(2)
for idx, db in enumerate(databases):
    with db_cols[idx % 2]:
        db_status = check_database(db["url"])

        status_class = {
            "healthy": "status-healthy",
            "missing": "status-unknown",
            "error": "status-unhealthy",
        }.get(db_status["status"], "status-unknown")

        status_text = {
            "healthy": "✓ Accessible",
            "missing": "⚠ Not Found",
            "error": "✗ Error",
        }.get(db_status["status"], "? Unknown")

        st.markdown(
            f"""
            <div class="status-card">
                <h3>🗄️ {db['name']}</h3>
                <p style="color: #64748b; font-size: 0.9rem; margin-bottom: 1rem;">{db['description']}</p>
                <div class="metric-row">
                    <span class="metric-label">Status:</span>
                    <span class="status-badge {status_class}">{status_text}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">URL:</span>
                    <span class="metric-value" style="font-family: monospace; font-size: 0.85rem;">{db['url']}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Tables:</span>
                    <span class="metric-value">{db_status.get('tables', 0)}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.divider()

# Environment Information
st.subheader("⚙️ Environment Configuration")

env_info = get_environment_info()

env_col1, env_col2 = st.columns(2)

with env_col1:
    st.markdown(
        f"""
        <div class="status-card">
            <h3>🐍 Runtime</h3>
            <div class="metric-row">
                <span class="metric-label">Python:</span>
                <span class="metric-value">{env_info['python_version']}</span>
            </div>
            <div class="metric-row">
                <span class="metric-label">Deployment:</span>
                <span class="metric-value">{env_info['deployment_mode']}</span>
            </div>
            <div class="metric-row">
                <span class="metric-label">Database:</span>
                <span class="metric-value" style="font-family: monospace; font-size: 0.8rem;">{env_info['database_url'][:40]}...</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with env_col2:
    ollama_status = "Enabled" if env_info['ollama_enabled'].lower() in ("true", "1", "yes") else "Disabled"
    ollama_class = "status-healthy" if ollama_status == "Enabled" else "status-info"

    st.markdown(
        f"""
        <div class="status-card">
            <h3>🤖 AI Configuration</h3>
            <div class="metric-row">
                <span class="metric-label">Ollama:</span>
                <span class="status-badge {ollama_class}">{ollama_status}</span>
            </div>
            <div class="metric-row">
                <span class="metric-label">Model:</span>
                <span class="metric-value">{env_info['ollama_model']}</span>
            </div>
            <div class="metric-row">
                <span class="metric-label">Scheduler:</span>
                <span class="metric-value" style="font-family: monospace; font-size: 0.8rem;">{env_info['scheduler_url'][:30]}...</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.divider()

# Quick Actions
st.subheader("🚀 Quick Actions")

action_col1, action_col2 = st.columns(2)

with action_col1:
    if st.button("🔄 Refresh Status", use_container_width=True):
        st.rerun()

with action_col2:
    if st.button("📊 View Logs", use_container_width=True):
        st.info("Use `docker-compose logs -f` or `./scripts/dev-logs.ps1` to view service logs")

# System Health Summary
st.divider()

healthy_count = sum(1 for r in service_results if r["status"] == "healthy")
total_services = len(service_results)
health_percentage = (healthy_count / total_services * 100) if total_services > 0 else 0

db_healthy = sum(1 for db in databases if check_database(db["path"])["status"] == "healthy")
db_total = len(databases)

summary_col1, summary_col2, summary_col3 = st.columns(3)

summary_col1.metric("Services Online", f"{healthy_count}/{total_services}", f"{health_percentage:.0f}%")
summary_col2.metric("Databases", f"{db_healthy}/{db_total}", "Accessible" if db_healthy == db_total else "Check status")
summary_col3.metric("Last Check", datetime.now().strftime("%H:%M:%S"), "Live")

if health_percentage < 100:
    st.warning(
        "⚠️ Some services are not responding. Make sure Docker Compose is running with `./scripts/dev-start.ps1` "
        "or check service logs for errors."
    )
else:
    st.success("✅ All core services are operational!")

# ==============================================================================
# UPTIME HISTORY
# ==============================================================================
st.divider()
st.subheader("📈 Uptime History")

# Store uptime history
if "system_uptime_history" not in st.session_state:
    st.session_state.system_uptime_history = []

# Add current check to history
entry = {
    "timestamp": datetime.now().isoformat(),
    "healthy": healthy_count,
    "total": total_services,
    "pct": health_percentage,
}

# Only add if different from last entry or if enough time passed
if not st.session_state.system_uptime_history or \
   (datetime.now() - datetime.fromisoformat(st.session_state.system_uptime_history[-1]["timestamp"])).seconds > 30:
    st.session_state.system_uptime_history.append(entry)
    # Keep only last 100 entries
    st.session_state.system_uptime_history = st.session_state.system_uptime_history[-100:]

# Display uptime chart
if len(st.session_state.system_uptime_history) > 1:
    import pandas as pd
    import plotly.graph_objects as go

    df = pd.DataFrame(st.session_state.system_uptime_history)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=df["pct"],
        mode='lines+markers',
        name='Uptime %',
        line=dict(color='#10B981', width=3),
        fill='tozeroy',
        fillcolor='rgba(16, 185, 129, 0.2)',
    ))

    fig.update_layout(
        title="Service Uptime Over Time",
        xaxis_title="Time",
        yaxis_title="Uptime %",
        yaxis=dict(range=[0, 105]),
        height=280,
        margin=dict(l=20, r=20, t=40, b=20),
        hovermode='x unified',
    )

    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Uptime history will appear after multiple status checks.")

# ==============================================================================
# QUICK NAVIGATION
# ==============================================================================
st.divider()
st.subheader("🗺️ Quick Navigation")
st.caption("Jump to related pages for more details")

nav_cols = st.columns(4)

with nav_cols[0]:
    if st.button("🔌 MCP Servers", use_container_width=True):
        st.switch_page("pages/13_MCP_Servers.py")

with nav_cols[1]:
    if st.button("🐳 Docker", use_container_width=True):
        st.switch_page("pages/8_Docker_MCP_Test.py")

with nav_cols[2]:
    if st.button("☸️ Kubernetes", use_container_width=True):
        st.switch_page("pages/6_Kubernetes.py")

with nav_cols[3]:
    if st.button("⏱️ Scheduler", use_container_width=True):
        st.switch_page("pages/10_MCP_Scheduler.py")

# Footer note
st.caption(
    "💡 **Tip:** Enable auto-refresh to monitor services in real-time. "
    "Services are checked every time this page loads."
)
