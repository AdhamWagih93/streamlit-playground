from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class PageSpec:
    path: str
    title: str
    icon: str
    group: str
    always_visible: bool = False


def get_page_catalog() -> List[PageSpec]:
    """Single source of truth for Streamlit navigation pages."""

    return [
        PageSpec("pages/0_Home.py", "Home", "🏠", "Home", always_visible=True),
        PageSpec("pages/1_Team_Task_Manager.py", "Team Task Manager", "📋", "Team"),
        PageSpec("pages/2_DevOps_Referral_Agent.py", "DevOps Referral Agent", "🧑‍💼", "Team"),
        PageSpec("pages/3_WFH_Schedule.py", "WFH Schedule", "📅", "Team"),
        PageSpec("pages/4_DataGen_Agent.py", "DataGen Agent", "🧪", "AI Playground"),
        PageSpec("pages/5_Agent_Management.py", "Agent Management", "🧠", "AI Playground"),
        PageSpec("pages/6_Kubernetes.py", "Kubernetes", "☸️", "AI Playground"),
        PageSpec("pages/10_MCP_Scheduler.py", "Scheduler", "⏱️", "AI Playground"),
        PageSpec("pages/7_Setup.py", "Setup", "🛠️", "AI Playground"),
        PageSpec("pages/8_Docker_MCP_Test.py", "Docker MCP Test", "🐳", "AI Playground"),
        PageSpec("pages/9_Nexus_Explorer.py", "Nexus Explorer", "📦", "AI Playground"),
        PageSpec("pages/11_Database.py", "Database", "🗄️", "Admin"),
    ]


def catalog_by_group() -> Dict[str, List[PageSpec]]:
    grouped: Dict[str, List[PageSpec]] = {}
    for p in get_page_catalog():
        grouped.setdefault(p.group, []).append(p)
    return grouped


def known_page_paths() -> List[str]:
    return [p.path for p in get_page_catalog()]
