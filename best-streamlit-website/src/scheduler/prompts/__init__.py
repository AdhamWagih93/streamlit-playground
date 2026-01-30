from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="job_design",
        title="Job design",
        description="Help design scheduler jobs safely and predictably.",
        tags={"scheduler", "workflow"},
    )
    def job_design(goal: str) -> str:
        return """You are a scheduler operator.

Goal:
{goal}

Design rules:
- Prefer idempotent tools.
- Use the smallest interval that meets requirements.
- Include a clear label and expected output shape.
- Verify by listing jobs and recent runs.

Output:
- Proposed job spec (label/server/tool/args/interval)
- Then the tool calls to create and validate it
""".format(goal=goal)

    @mcp.prompt(
        name="incident_triage",
        title="Incident triage",
        description="Triage scheduler failures and propose evidence to collect.",
        tags={"scheduler"},
    )
    def incident_triage(symptoms: str) -> str:
        return """Triage steps:
1) List recent runs and failed jobs
2) Inspect the specific job config and args
3) Check downstream MCP server health
4) Propose mitigation (disable job, adjust interval, fix args)

Symptoms:
{symptoms}
""".format(symptoms=symptoms)
