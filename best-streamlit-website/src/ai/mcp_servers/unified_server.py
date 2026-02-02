"""Unified MCP Server with FastMCP 3.0.0 mounting and namespacing.

This module provides a unified approach to combining multiple MCP servers
using FastMCP 3.0.0's mount() feature with proper namespacing.

Key features:
- Dynamic mounting of MCP servers with namespace prefixes
- Tool names are automatically prefixed (e.g., kubernetes_list_pods)
- FileSystemProvider support for discovering tools from directories
- Compatible with the existing MCP server architecture

Example:
    unified = UnifiedMCPServer()
    unified.mount_server("kubernetes")
    unified.mount_server("docker")
    mcp = unified.build()
    mcp.run(transport="http")
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastmcp import FastMCP


# Available MCP server modules
MCP_SERVER_MODULES = {
    "jenkins": "src.ai.mcp_servers.jenkins.mcp",
    "kubernetes": "src.ai.mcp_servers.kubernetes.mcp",
    "docker": "src.ai.mcp_servers.docker.mcp",
    "nexus": "src.ai.mcp_servers.nexus.mcp",
    "git": "src.ai.mcp_servers.git.mcp",
    "trivy": "src.ai.mcp_servers.trivy.mcp",
    "playwright": "src.ai.mcp_servers.playwright.mcp",
    "websearch": "src.ai.mcp_servers.websearch.mcp",
    "local": "src.ai.mcp_servers.local.mcp",
    "sonarqube": "src.ai.mcp_servers.sonarqube.mcp",
}


@dataclass
class UnifiedMCPServer:
    """Builder for creating a unified MCP server from multiple sources.

    Uses FastMCP 3.0.0's mount() with namespace to prefix tool names
    and avoid collisions across servers.
    """

    name: str = "unified-mcp"
    mounted_servers: Dict[str, FastMCP] = field(default_factory=dict)
    namespaces: Set[str] = field(default_factory=set)
    _mcp: Optional[FastMCP] = field(default=None, init=False, repr=False)

    def mount_server(
        self,
        server_key: str,
        *,
        namespace: Optional[str] = None,
        use_namespace: bool = True,
    ) -> "UnifiedMCPServer":
        """Mount an MCP server by key.

        Args:
            server_key: Key from MCP_SERVER_MODULES (e.g., "kubernetes")
            namespace: Custom namespace prefix (defaults to server_key)
            use_namespace: If True, prefix tools with namespace

        Returns:
            self for chaining
        """
        if server_key not in MCP_SERVER_MODULES:
            raise ValueError(f"Unknown server: {server_key}. Available: {list(MCP_SERVER_MODULES.keys())}")

        module_path = MCP_SERVER_MODULES[server_key]
        mod = importlib.import_module(module_path)
        server_mcp = getattr(mod, "mcp", None)

        if server_mcp is None:
            raise ValueError(f"Module {module_path} does not have an 'mcp' attribute")

        ns = namespace or server_key if use_namespace else None
        self.mounted_servers[server_key] = server_mcp
        if ns:
            self.namespaces.add(ns)

        return self

    def mount_servers(
        self,
        server_keys: List[str],
        *,
        use_namespace: bool = True,
    ) -> "UnifiedMCPServer":
        """Mount multiple MCP servers at once.

        Args:
            server_keys: List of server keys to mount
            use_namespace: If True, prefix tools with server namespace

        Returns:
            self for chaining
        """
        for key in server_keys:
            self.mount_server(key, use_namespace=use_namespace)
        return self

    def mount_from_filesystem(
        self,
        root_path: Path,
        *,
        namespace: Optional[str] = None,
        reload: bool = False,
    ) -> "UnifiedMCPServer":
        """Mount tools discovered from a filesystem directory.

        Uses FastMCP's FileSystemProvider to discover @tool, @resource,
        and @prompt decorated functions in Python files.

        Args:
            root_path: Directory to scan for tools
            namespace: Optional namespace prefix for discovered tools
            reload: If True, re-scan on each request (dev mode)

        Returns:
            self for chaining
        """
        from fastmcp.server.providers.filesystem import FileSystemProvider

        provider = FileSystemProvider(root=root_path, reload=reload)
        fs_mcp = FastMCP(f"fs-{namespace or root_path.name}", providers=[provider])

        key = namespace or root_path.name
        self.mounted_servers[key] = fs_mcp
        if namespace:
            self.namespaces.add(namespace)

        return self

    def build(self) -> FastMCP:
        """Build the unified MCP server with all mounted servers.

        Returns:
            The configured FastMCP server ready to run
        """
        if self._mcp is not None:
            return self._mcp

        self._mcp = FastMCP(self.name)

        for server_key, server_mcp in self.mounted_servers.items():
            # Use namespace if server_key is in namespaces set
            ns = server_key if server_key in self.namespaces else None
            try:
                self._mcp.mount(server_mcp, namespace=ns)
            except Exception as exc:
                # Some servers might fail to mount; log and continue
                print(f"Warning: Failed to mount {server_key}: {exc}")

        return self._mcp

    def get_tool_names(self) -> List[str]:
        """Get all tool names from the unified server.

        Returns:
            List of tool names (namespaced if applicable)
        """
        mcp = self.build()
        tools = mcp.get_tools()
        return [t.name for t in tools]


def create_unified_server(
    selected_servers: List[str],
    *,
    use_namespace: bool = True,
    server_name: str = "unified-mcp",
) -> FastMCP:
    """Convenience function to create a unified MCP server.

    Args:
        selected_servers: List of server keys to include
        use_namespace: If True, prefix tools with server namespace
        server_name: Name for the unified server

    Returns:
        Configured FastMCP server

    Example:
        mcp = create_unified_server(["kubernetes", "docker"])
        mcp.run(transport="http", port=8080)
    """
    builder = UnifiedMCPServer(name=server_name)
    builder.mount_servers(selected_servers, use_namespace=use_namespace)
    return builder.build()


def get_server_module(server_key: str) -> str:
    """Get the module path for a server key."""
    if server_key not in MCP_SERVER_MODULES:
        raise ValueError(f"Unknown server: {server_key}")
    return MCP_SERVER_MODULES[server_key]


def list_available_servers() -> List[str]:
    """List all available MCP server keys."""
    return list(MCP_SERVER_MODULES.keys())
