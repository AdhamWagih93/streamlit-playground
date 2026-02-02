"""FileSystemProvider-compatible MCP tools.

This directory contains tools that can be auto-discovered by FastMCP's
FileSystemProvider. Tools are defined using the @tool decorator from
fastmcp.tools.

Usage:
    from fastmcp import FastMCP
    from fastmcp.server.providers.filesystem import FileSystemProvider
    from pathlib import Path

    mcp = FastMCP("my-server", providers=[
        FileSystemProvider(Path(__file__).parent / "mcp_tools")
    ])
"""
