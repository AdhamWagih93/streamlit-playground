"""MCP Log database models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, Text, Boolean, Integer, Float, DateTime, Index
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _generate_id() -> str:
    """Generate a unique ID for log entries."""
    return str(uuid.uuid4())


class MCPToolCall(Base):
    """Model for storing MCP tool call logs.

    Captures all relevant information about each MCP tool invocation
    for monitoring, debugging, and analytics purposes.
    """

    __tablename__ = "mcp_tool_calls"

    # Primary key
    id = Column(String(36), primary_key=True, default=_generate_id)

    # Request identification
    request_id = Column(String(64), nullable=True, index=True)  # For correlating related calls
    session_id = Column(String(64), nullable=True, index=True)  # For tracking user sessions

    # Server and tool information
    server_name = Column(String(64), nullable=False, index=True)
    tool_name = Column(String(128), nullable=False, index=True)

    # Request details
    args_json = Column(Text, nullable=True)  # JSON-serialized arguments (sensitive data redacted)
    args_hash = Column(String(64), nullable=True)  # Hash of args for deduplication

    # Response details
    success = Column(Boolean, nullable=False, default=False, index=True)
    result_preview = Column(Text, nullable=True)  # Truncated result for debugging
    error_message = Column(Text, nullable=True)
    error_type = Column(String(128), nullable=True)

    # Timing
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Float, nullable=True, index=True)  # Milliseconds

    # Context
    source = Column(String(64), nullable=True, index=True)  # e.g., "agent_builder", "kubernetes_page"
    user_id = Column(String(64), nullable=True, index=True)  # Optional user tracking

    # Metadata
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Indexes for common queries
    __table_args__ = (
        Index("ix_mcp_tool_calls_server_tool", "server_name", "tool_name"),
        Index("ix_mcp_tool_calls_started_success", "started_at", "success"),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "server_name": self.server_name,
            "tool_name": self.tool_name,
            "args_json": self.args_json,
            "success": self.success,
            "result_preview": self.result_preview,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "source": self.source,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class MCPServerHealth(Base):
    """Model for tracking MCP server health over time.

    Stores periodic health check results for monitoring server availability.
    """

    __tablename__ = "mcp_server_health"

    id = Column(String(36), primary_key=True, default=_generate_id)
    server_name = Column(String(64), nullable=False, index=True)
    checked_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    healthy = Column(Boolean, nullable=False, default=False)
    response_time_ms = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    details_json = Column(Text, nullable=True)  # JSON health check response

    __table_args__ = (
        Index("ix_mcp_server_health_server_checked", "server_name", "checked_at"),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "server_name": self.server_name,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "healthy": self.healthy,
            "response_time_ms": self.response_time_ms,
            "error_message": self.error_message,
            "details_json": self.details_json,
        }
