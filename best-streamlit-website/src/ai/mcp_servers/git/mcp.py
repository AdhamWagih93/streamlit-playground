"""Git MCP Server with modular tool organization.

This server organizes tools in the components/tools/ directory and registers
them dynamically with the FastMCP instance.
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from .config import GitMCPServerConfig
from ..cache import configure_mcp_cache

# Import tool implementations from modular structure
from .components.tools.core import (
    git_health_check,
    git_is_repo,
    git_status,
    git_log,
    git_branches,
    git_current_branch,
    git_diff,
    git_show,
    git_remotes,
    git_tags,
    git_blame,
    git_stash_list,
    git_config_get,
    git_fetch,
    git_pull,
    git_checkout,
    git_clone,
)

from .prompts import register_prompts


# Create the MCP server
mcp = FastMCP("git-mcp")

# Configure optional Redis caching
configure_mcp_cache(mcp, server_name="git")

# Register tools using mcp.tool()
mcp.tool(git_health_check)
mcp.tool(git_is_repo)
mcp.tool(git_status)
mcp.tool(git_log)
mcp.tool(git_branches)
mcp.tool(git_current_branch)
mcp.tool(git_diff)
mcp.tool(git_show)
mcp.tool(git_remotes)
mcp.tool(git_tags)
mcp.tool(git_blame)
mcp.tool(git_stash_list)
mcp.tool(git_config_get)
mcp.tool(git_fetch)
mcp.tool(git_pull)
mcp.tool(git_checkout)
mcp.tool(git_clone)

# Prompts
register_prompts(mcp)


def run_stdio() -> None:
    """Run the Git MCP server over HTTP.

    The function name is kept for backwards compatibility with existing
    entrypoints, but the server no longer supports stdio transport.
    """

    cfg = GitMCPServerConfig.from_env()

    host = os.environ.get("MCP_HOST") or cfg.mcp_host
    port_raw = os.environ.get("MCP_PORT")
    try:
        port = int(port_raw) if port_raw else int(cfg.mcp_port)
    except Exception:
        port = int(cfg.mcp_port)

    try:
        mcp.run(transport="http", host=host, port=port)
    except TypeError:
        mcp.run(transport="http")


if __name__ == "__main__":
    run_stdio()
