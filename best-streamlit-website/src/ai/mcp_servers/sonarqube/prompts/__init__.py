from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="guided_workflow",
        title="Guided workflow",
        description="Plan SonarQube investigations using SonarQube MCP tools.",
        tags={"sonarqube", "workflow"},
    )
    def guided_workflow(goal: str) -> str:
        return """You are a SonarQube analyst.

Goal:
{goal}

Approach:
1) Confirm server status (`sonarqube_get_system_status`, `sonarqube_get_system_health`).
2) Identify project and branch.
3) Gather issues, hotspots, and quality gate details.
4) Summarize findings with actionable remediation.

Output:
- Tool calls (in order)
- Then a structured summary
""".format(goal=goal)

    @mcp.prompt(
        name="security_review",
        title="Security review template",
        description="Create a security-focused review plan for a project.",
        tags={"sonarqube", "security"},
    )
    def security_review(project_key: str) -> str:
        return """Create a security review checklist for SonarQube project:
{project_key}

Include:
- Key metrics to fetch
- Which issue types to prioritize
- A reporting structure for findings
""".format(project_key=project_key)
