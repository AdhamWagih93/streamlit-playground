from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="guided_workflow",
        title="Guided workflow",
        description="Plan Nexus investigations/actions using Nexus MCP tools.",
        tags={"nexus", "workflow"},
    )
    def guided_workflow(goal: str) -> str:
        return """You are a Nexus Repository operator.

Goal:
{goal}

Approach:
1) Verify Nexus health.
2) Identify repository, component, or asset.
3) Prefer listing/searching before modifying.
4) If deleting/promoting assets, confirm coordinates explicitly.

Output:
- Plan
- Tool calls
""".format(goal=goal)

    @mcp.prompt(
        name="artifact_triage",
        title="Artifact triage",
        description="Triage missing/broken artifacts and propose next checks.",
        tags={"nexus"},
    )
    def artifact_triage(coordinates: str) -> str:
        return """Triage steps for the artifact coordinates below.
Include:
- What to check in Nexus
- Which tool calls to run
- Likely root causes

Coordinates:
{coordinates}
""".format(coordinates=coordinates)
