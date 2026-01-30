from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="guided_workflow",
        title="Guided workflow",
        description="Plan Git operations safely using Git MCP tools.",
        tags={"git", "workflow"},
    )
    def guided_workflow(goal: str, repo_path: str = ".") -> str:
        return """You are an expert Git assistant.

Goal:
{goal}

Repo path:
{repo_path}

Approach:
1) Confirm it's a repo: `git_is_repo`.
2) Inspect state: `git_status`, then optionally `git_current_branch`, `git_log`.
3) If changing state, prefer safe operations and verify after each step.

Output format:
- A short plan
- The tool calls to make with args (include `repo_path`)
""".format(goal=goal, repo_path=repo_path)

    @mcp.prompt(
        name="review_changes",
        title="Review changes",
        description="Guide a careful diff/review workflow.",
        tags={"git"},
    )
    def review_changes(repo_path: str = ".") -> str:
        return """Review workflow:
1) `git_status` to understand modified files
2) `git_diff` for working tree
3) `git_log` for recent history
4) Use `git_show` for specific commits

Return the exact tool calls in order, each including `repo_path`.
""".format(repo_path=repo_path)
