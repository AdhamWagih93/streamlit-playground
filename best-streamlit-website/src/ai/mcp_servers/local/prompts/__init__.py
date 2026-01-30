from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="guided_workflow",
        title="Guided workflow",
        description="Plan safe local file operations using Local MCP tools.",
        tags={"local", "workflow"},
    )
    def guided_workflow(goal: str, root: str = "") -> str:
        return """You are a careful local filesystem assistant.

Goal:
{goal}

Root (if provided):
{root}

Rules:
- Prefer read-only operations (list/search) before write/delete.
- If deleting/changing files, confirm exact paths and show a preview.

Output:
- Plan
- Tool calls with exact paths
""".format(goal=goal, root=root)

    @mcp.prompt(
        name="codebase_orientation",
        title="Codebase orientation",
        description="How to quickly orient in a repo using Local tools.",
        tags={"local"},
    )
    def codebase_orientation(topic: str = "") -> str:
        return """Create a quick orientation checklist.

Topic (optional):
{topic}

Include suggested Local MCP tool calls for:
- listing directories
- searching for symbols
- reading key files (README, config)
""".format(topic=topic)
