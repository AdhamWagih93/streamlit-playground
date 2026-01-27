"""MCP Log - Universal logging for all MCP tool interactions.

This module provides:
- Database models for storing MCP tool call logs
- Repository functions for CRUD operations
- Interceptor for automatic logging in MCP clients
- Support for PostgreSQL (default) and SQLite (optional)
"""

from .repo import (
    init_db,
    log_tool_call,
    get_tool_calls,
    get_tool_call,
    get_tool_call_stats,
    get_server_stats,
    get_hourly_stats,
    get_recent_errors,
    cleanup_old_logs,
)

from .interceptor import create_logging_interceptor, get_logged_mcp_client

__all__ = [
    "init_db",
    "log_tool_call",
    "get_tool_calls",
    "get_tool_call",
    "get_tool_call_stats",
    "get_server_stats",
    "get_hourly_stats",
    "get_recent_errors",
    "cleanup_old_logs",
    "create_logging_interceptor",
    "get_logged_mcp_client",
]
