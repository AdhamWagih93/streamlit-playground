"""Trivy MCP server package.

Structure mirrors other MCP server layouts:
- config.py: Server configuration from environment
- mcp.py: FastMCP tool definitions + HTTP runner
- utils/: Trivy client wrapper and business logic

Trivy is a comprehensive security scanner for:
- Container images
- Filesystems
- Git repositories
- Infrastructure as Code (Terraform, K8s, etc.)
- SBOM (Software Bill of Materials)
"""

from .mcp import mcp, run_stdio

__all__ = ["mcp", "run_stdio"]
