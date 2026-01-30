from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="guided_workflow",
        title="Guided workflow",
        description="Step-by-step plan for accomplishing a goal using Docker MCP tools.",
        tags={"docker", "workflow"},
    )
    def guided_workflow(goal: str, constraints: str = "") -> str:
        return """You are an expert Docker operator.

Goal:
{goal}

Constraints (optional):
{constraints}

Approach:
1) Confirm Docker health using `health_check`.
2) Discover relevant resources:
   - containers: `list_containers`
   - images: `list_images`
   - networks/volumes: `list_networks`, `list_volumes`
3) Execute the minimum necessary actions (start/stop/restart/remove, pull/build/tag/push).
4) Validate by re-listing and (if needed) checking logs with `container_logs`.

Output format:
- A short plan (bullets)
- Then the exact tool calls to make, in order, with arguments
""".format(goal=goal, constraints=constraints)

    @mcp.prompt(
        name="tool_picker",
        title="Tool picker",
        description="Suggest the best Docker MCP tool(s) to call for a user request.",
        tags={"docker"},
    )
    def tool_picker(request: str) -> str:
        return """Given the request below, pick the 1-3 best Docker MCP tools and explain why.

Request:
{request}

Rules:
- Prefer read-only tools first (list/status) when unsure.
- Prefer `restart_container` over stop+start when appropriate.
- Use `container_logs` only after identifying a specific container.

Return:
- tool_names: [..]
- reasoning: ..
- suggested_args: {{ tool_name: args }}
""".format(request=request)
