from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="guided_workflow",
        title="Guided workflow",
        description="Plan Kubernetes investigations safely using Kubernetes MCP tools.",
        tags={"kubernetes", "workflow"},
    )
    def guided_workflow(goal: str, namespace: str = "default") -> str:
        return """You are a Kubernetes SRE assistant.

Goal:
{goal}

Default namespace:
{namespace}

Approach:
1) Start with cluster health (`health_check`).
2) Narrow scope: namespace, workload, pod(s).
3) Use read-only discovery first (list/describe/get logs/events).
4) Only then propose changes (rollout restart/scale/apply) and validate.

Output format:
- A plan
- Tool calls (read-only first)
- Risks + rollback
""".format(goal=goal, namespace=namespace)

    @mcp.prompt(
        name="runbook",
        title="Runbook template",
        description="Create a compact runbook for recurring Kubernetes issues.",
        tags={"kubernetes"},
    )
    def runbook(issue: str) -> str:
        return """Write a runbook with:
- Preconditions
- Detection (signals + which tool calls)
- Diagnosis steps
- Mitigation steps
- Verification steps

Issue:
{issue}
""".format(issue=issue)
