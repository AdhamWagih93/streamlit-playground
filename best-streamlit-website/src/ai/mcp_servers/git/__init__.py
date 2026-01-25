"""Git MCP server package.

Structure mirrors other MCP server layouts:
- config.py: Server configuration from environment
- mcp.py: FastMCP tool definitions + HTTP runner
- utils/: Git client wrapper and business logic
"""

from .mcp import mcp, run_stdio

__all__ = ["mcp", "run_stdio"]
