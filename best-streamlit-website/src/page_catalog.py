"""Page catalog for BSW Platform.

Defines the navigation structure and page organization.
Pages are grouped into logical categories for better UX.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class PageSpec:
    """Specification for a navigation page."""

    path: str
    title: str
    icon: str
    group: str
    description: str = ""
    always_visible: bool = False


# Group display order and styling
GROUP_ORDER = ["Home", "Team", "DevOps", "AI & Agents", "Platform"]

GROUP_ICONS = {
    "Home": "🏠",
    "Team": "👥",
    "DevOps": "🔧",
    "AI & Agents": "🤖",
    "Platform": "⚙️",
}

GROUP_DESCRIPTIONS = {
    "Home": "Dashboard and system overview",
    "Team": "Team management and scheduling",
    "DevOps": "Infrastructure and deployment tools",
    "AI & Agents": "AI-powered tools and agents",
    "Platform": "System administration and monitoring",
}


def get_page_catalog() -> List[PageSpec]:
    """Single source of truth for Streamlit navigation pages.

    Pages are organized into logical groups:
    - Home: Dashboard and status monitoring
    - Team: Team collaboration and scheduling
    - DevOps: Infrastructure and CI/CD tools
    - AI & Agents: AI-powered automation
    - Platform: System administration
    """
    return [
        # =====================================================================
        # HOME - Dashboard and Status
        # =====================================================================
        PageSpec(
            path="pages/0_Home.py",
            title="Home",
            icon="🏠",
            group="Home",
            description="Platform dashboard",
            always_visible=True,
        ),
        PageSpec(
            path="pages/12_System_Status.py",
            title="System Status",
            icon="📊",
            group="Home",
            description="Health monitoring",
            always_visible=True,
        ),
        # =====================================================================
        # TEAM - Team Management & Scheduling
        # =====================================================================
        PageSpec(
            path="pages/1_Team_Task_Manager.py",
            title="Task Manager",
            icon="📋",
            group="Team",
            description="Kanban board and task tracking",
        ),
        PageSpec(
            path="pages/3_WFH_Schedule.py",
            title="WFH Schedule",
            icon="📅",
            group="Team",
            description="Work from home scheduling",
        ),
        PageSpec(
            path="pages/2_DevOps_Referral_Agent.py",
            title="Referral Agent",
            icon="🧑‍💼",
            group="Team",
            description="DevOps task referral",
        ),
        # =====================================================================
        # DEVOPS - Infrastructure & Deployment
        # =====================================================================
        PageSpec(
            path="pages/6_Kubernetes.py",
            title="Kubernetes",
            icon="☸️",
            group="DevOps",
            description="K8s cluster management",
        ),
        PageSpec(
            path="pages/8_Docker_MCP_Test.py",
            title="Docker",
            icon="🐳",
            group="DevOps",
            description="Container management",
        ),
        PageSpec(
            path="pages/14_Git_Explorer.py",
            title="Git Explorer",
            icon="📂",
            group="DevOps",
            description="Repository browser",
        ),
        PageSpec(
            path="pages/15_Trivy_Scanner.py",
            title="Security Scanner",
            icon="🔒",
            group="DevOps",
            description="Vulnerability scanning",
        ),
        PageSpec(
            path="pages/9_Nexus_Explorer.py",
            title="Nexus Artifacts",
            icon="📦",
            group="DevOps",
            description="Artifact repository",
        ),
        # =====================================================================
        # AI & AGENTS - AI-Powered Tools
        # =====================================================================
        PageSpec(
            path="pages/16_Agent_Builder.py",
            title="Agent Builder",
            icon="🤖",
            group="AI & Agents",
            description="Build custom AI agents",
        ),
        PageSpec(
            path="pages/5_Agent_Management.py",
            title="Agent Management",
            icon="🧠",
            group="AI & Agents",
            description="Manage AI agents",
        ),
        PageSpec(
            path="pages/4_DataGen_Agent.py",
            title="DataGen Agent",
            icon="🧪",
            group="AI & Agents",
            description="Test data generation",
        ),
        PageSpec(
            path="pages/18_Playwright_Browser.py",
            title="Browser Automation",
            icon="🎭",
            group="AI & Agents",
            description="Web automation",
        ),
        PageSpec(
            path="pages/19_Web_Search.py",
            title="Web Search",
            icon="🔍",
            group="AI & Agents",
            description="AI-powered search",
        ),
        PageSpec(
            path="pages/21_Agent_Lab.py",
            title="Agent Lab",
            icon="🧪",
            group="AI & Agents",
            description="Test Normal/RAG/Deep agents",
        ),
        # =====================================================================
        # PLATFORM - Administration & Monitoring
        # =====================================================================
        PageSpec(
            path="pages/13_MCP_Servers.py",
            title="MCP Servers",
            icon="🔌",
            group="Platform",
            description="Server connections",
            always_visible=True,
        ),
        PageSpec(
            path="pages/10_MCP_Scheduler.py",
            title="Job Scheduler",
            icon="⏱️",
            group="Platform",
            description="Scheduled tasks",
        ),
        PageSpec(
            path="pages/17_MCP_Log.py",
            title="Activity Log",
            icon="📝",
            group="Platform",
            description="Tool call history",
        ),
        PageSpec(
            path="pages/20_Local_File_System.py",
            title="Local Files",
            icon="🗂️",
            group="Platform",
            description="Local file operations",
        ),
        PageSpec(
            path="pages/11_Database.py",
            title="Database",
            icon="🗄️",
            group="Platform",
            description="Database explorer",
        ),
    ]


def catalog_by_group() -> Dict[str, List[PageSpec]]:
    """Get pages organized by group.

    Returns pages in the order defined by GROUP_ORDER.
    """
    grouped: Dict[str, List[PageSpec]] = {}
    for p in get_page_catalog():
        grouped.setdefault(p.group, []).append(p)

    # Return in defined order
    ordered: Dict[str, List[PageSpec]] = {}
    for group in GROUP_ORDER:
        if group in grouped:
            ordered[group] = grouped[group]

    # Add any remaining groups not in GROUP_ORDER
    for group, pages in grouped.items():
        if group not in ordered:
            ordered[group] = pages

    return ordered


def known_page_paths() -> List[str]:
    """Get all known page paths."""
    return [p.path for p in get_page_catalog()]


def get_group_icon(group: str) -> str:
    """Get the icon for a group."""
    return GROUP_ICONS.get(group, "📁")


def get_group_description(group: str) -> str:
    """Get the description for a group."""
    return GROUP_DESCRIPTIONS.get(group, "")
