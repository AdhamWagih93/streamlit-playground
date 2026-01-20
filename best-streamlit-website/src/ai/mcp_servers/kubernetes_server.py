"""Legacy entrypoint for the Kubernetes MCP server.

The actual FastMCP tool definitions are in `src.ai.mcp_servers.kubernetes.mcp`.
Business logic is in `src.ai.mcp_servers.kubernetes.service`.

This file intentionally adjusts sys.path so it can be executed directly:
`python src/ai/mcp_servers/kubernetes_server.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_project_root_on_syspath() -> None:
    # This file lives at: <root>/src/ai/mcp_servers/kubernetes_server.py
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


_ensure_project_root_on_syspath()

from src.ai.mcp_servers.kubernetes.mcp import run_stdio  # noqa: E402


if __name__ == "__main__":
    run_stdio()
