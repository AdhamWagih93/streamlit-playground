"""Trivy MCP Server with modular tool organization.

This server organizes tools in the components/tools/ directory and registers
them dynamically with the FastMCP instance.
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from .config import TrivyMCPServerConfig
from ..cache import configure_mcp_cache

from .prompts import register_prompts

# Import tool implementations from modular structure
from .components.tools.scanning import (
    trivy_health_check,
    trivy_update_db,
    trivy_scan_image,
    trivy_scan_filesystem,
    trivy_scan_repo,
    trivy_scan_config,
    trivy_scan_sbom,
    trivy_generate_sbom,
    trivy_list_plugins,
    trivy_clean_cache,
)


# Create the MCP server
mcp = FastMCP("trivy-mcp")

# Configure optional Redis caching
configure_mcp_cache(mcp, server_name="trivy")

# Prompts
register_prompts(mcp)

# Register tools using mcp.tool()
mcp.tool(trivy_health_check)
mcp.tool(trivy_update_db)
mcp.tool(trivy_scan_image)
mcp.tool(trivy_scan_filesystem)
mcp.tool(trivy_scan_repo)
mcp.tool(trivy_scan_config)
mcp.tool(trivy_scan_sbom)
mcp.tool(trivy_generate_sbom)
mcp.tool(trivy_list_plugins)
mcp.tool(trivy_clean_cache)


def run_stdio() -> None:
    """Run the Trivy MCP server over HTTP.

    The function name is kept for backwards compatibility with existing
    entrypoints, but the server no longer supports stdio transport.
    """

    cfg = TrivyMCPServerConfig.from_env()

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
