from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="guided_workflow",
        title="Guided workflow",
        description="Plan Jenkins investigations and actions using Jenkins MCP tools.",
        tags={"jenkins", "workflow"},
    )
    def guided_workflow(goal: str) -> str:
        return """You are a Jenkins troubleshooting assistant.

Goal:
{goal}

Approach:
1) Verify Jenkins connectivity and auth (server info / health).
2) Identify the job/pipeline and fetch its status and recent runs.
3) For failures, retrieve console output/logs and summarize root cause.
4) Propose minimal remediation steps.

Output format:
- A concise plan
- Tool calls (in order)
- Then a short diagnosis + next actions
""".format(goal=goal)

    @mcp.prompt(
        name="incident_summary",
        title="Incident summary",
        description="Structure a Jenkins incident summary.",
        tags={"jenkins"},
    )
    def incident_summary(symptoms: str) -> str:
        return """Create an incident summary with:
- Impact
- Timeline
- Suspected cause
- Evidence to collect (which Jenkins MCP tools to call)
- Immediate mitigation
- Longer-term fix

Symptoms:
{symptoms}
""".format(symptoms=symptoms)
