"""Docker MCP Server with modular tool organization.

This server organizes tools in the mcp/tools/ directory and registers
them dynamically with the FastMCP instance.
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from .config import DockerMCPServerConfig
from ..cache import configure_mcp_cache

# Import tool implementations from modular structure
from .components.tools.health import health_check
from .components.tools.containers import (
    list_containers,
    start_container,
    stop_container,
    restart_container,
    remove_container,
    container_logs,
)
from .components.tools.images import (
    list_images,
    pull_image,
    docker_login,
    build_image,
    tag_image,
    push_image,
    remove_image,
)
from .components.tools.networks import (
    list_networks,
    list_volumes,
)

from .prompts import register_prompts


# Create the MCP server
mcp = FastMCP("docker-mcp")

# Configure optional Redis caching
configure_mcp_cache(mcp, server_name="docker")

# Register tools using the @mcp.tool decorator
# Health tools
mcp.tool(health_check)

# Container tools
mcp.tool(list_containers)
mcp.tool(start_container)
mcp.tool(stop_container)
mcp.tool(restart_container)
mcp.tool(remove_container)
mcp.tool(container_logs)

# Image tools
mcp.tool(list_images)
mcp.tool(pull_image)
mcp.tool(docker_login)
mcp.tool(build_image)
mcp.tool(tag_image)
mcp.tool(push_image)
mcp.tool(remove_image)

# Network/volume tools
mcp.tool(list_networks)
mcp.tool(list_volumes)

# Prompts
register_prompts(mcp)


def run_stdio() -> None:
    """Run the Docker MCP server over HTTP.

    The function name is kept for backwards compatibility with existing
    entrypoints, but the server no longer supports stdio transport.
    """

    cfg = DockerMCPServerConfig.from_env()

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
