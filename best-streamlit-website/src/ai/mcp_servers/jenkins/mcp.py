"""Jenkins MCP Server with modular tool organization.

This server organizes tools in the components/tools/ directory and registers
them dynamically with the FastMCP instance.
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from .config import JenkinsMCPServerConfig
from ..cache import configure_mcp_cache

from .prompts import register_prompts

# Import tool implementations from modular structure
from .components.tools.server import (
    get_server_info,
    get_system_info,
)
from .components.tools.jobs import (
    list_jobs,
    get_job_info,
    disable_job,
    enable_job,
    delete_job,
    copy_job,
    get_job_config_xml,
    update_job_config_xml,
    search_jobs,
    create_inline_pipeline_job,
    create_scm_pipeline_job,
)
from .components.tools.builds import (
    list_builds,
    get_last_build_info,
    get_build_info,
    get_build_console,
    trigger_build,
    get_queue,
    cancel_queue_item,
    list_artifacts,
    get_build_changes,
)
from .components.tools.admin import (
    list_nodes,
    get_node_info,
    list_views,
    get_view_info,
    list_plugins,
)


# Create the MCP server
mcp = FastMCP("jenkins-mcp")

# Configure optional Redis caching
configure_mcp_cache(mcp, server_name="jenkins")

# Prompts
register_prompts(mcp)

# Register tools using mcp.tool()
# Server tools
mcp.tool(get_server_info)
mcp.tool(get_system_info)

# Job tools
mcp.tool(list_jobs)
mcp.tool(get_job_info)
mcp.tool(disable_job)
mcp.tool(enable_job)
mcp.tool(delete_job)
mcp.tool(copy_job)
mcp.tool(get_job_config_xml)
mcp.tool(update_job_config_xml)
mcp.tool(search_jobs)
mcp.tool(create_inline_pipeline_job)
mcp.tool(create_scm_pipeline_job)

# Build tools
mcp.tool(list_builds)
mcp.tool(get_last_build_info)
mcp.tool(get_build_info)
mcp.tool(get_build_console)
mcp.tool(trigger_build)
mcp.tool(get_queue)
mcp.tool(cancel_queue_item)
mcp.tool(list_artifacts)
mcp.tool(get_build_changes)

# Admin tools
mcp.tool(list_nodes)
mcp.tool(get_node_info)
mcp.tool(list_views)
mcp.tool(get_view_info)
mcp.tool(list_plugins)


def run_stdio() -> None:
    """Run the Jenkins MCP server over HTTP.

    The function name is kept for backwards compatibility with existing
    entrypoints, but the server no longer supports stdio transport.
    """

    cfg = JenkinsMCPServerConfig.from_env()

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
