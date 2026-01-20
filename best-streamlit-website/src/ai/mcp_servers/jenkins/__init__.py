"""Jenkins MCP server package.

Structure mirrors the Kubernetes MCP server layout:
- mcp.py: FastMCP tool definitions + stdio runner
- utils/: categorized Jenkins client/business logic
"""

from .mcp import mcp, run_stdio  # re-export
