"""Nexus MCP Server with modular tool organization.

This server organizes tools in the components/tools/ directory and registers
them dynamically with the FastMCP instance.
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from .config import NexusMCPServerConfig
from ..cache import configure_mcp_cache

from .prompts import register_prompts

# Import tool implementations from modular structure
from .components.tools.core import (
    nexus_health_check,
    nexus_get_system_status,
    nexus_list_repositories,
    nexus_list_blobstores,
    nexus_search_components,
    nexus_list_assets,
    nexus_get_asset,
    nexus_list_users,
    nexus_list_roles,
    nexus_list_tasks,
    nexus_raw_request,
)


# Create the MCP server
mcp = FastMCP("nexus-mcp")

# Configure optional Redis caching
configure_mcp_cache(mcp, server_name="nexus")

# Prompts
register_prompts(mcp)

# Register tools using mcp.tool()
mcp.tool(nexus_health_check)
mcp.tool(nexus_get_system_status)
mcp.tool(nexus_list_repositories)
mcp.tool(nexus_list_blobstores)
mcp.tool(nexus_search_components)
mcp.tool(nexus_list_assets)
mcp.tool(nexus_get_asset)
mcp.tool(nexus_list_users)
mcp.tool(nexus_list_roles)
mcp.tool(nexus_list_tasks)
mcp.tool(nexus_raw_request)


def run_stdio() -> None:
    """Run the Nexus MCP server over HTTP.

    The function name is kept for backwards compatibility with existing
    entrypoints, but the server no longer supports stdio transport.
    """

    cfg = NexusMCPServerConfig.from_env()

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
