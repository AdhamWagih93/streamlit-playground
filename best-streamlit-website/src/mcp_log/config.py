"""MCP Log database configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from src.config_utils import env_int, env_optional_str, env_str


def _repo_root() -> str:
    """Get the repository root directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass(frozen=True)
class MCPLogConfig:
    """Configuration for MCP logging.

    Environment variables:
    - PLATFORM_DATABASE_URL: Shared database URL (used by all services)
    - MCP_LOG_DATABASE_URL: MCP log-specific database URL
    - MCP_LOG_RETENTION_DAYS: Number of days to keep logs (default: 30)
    - MCP_LOG_ENABLED: Enable/disable logging (default: true)

    Defaults to PostgreSQL using PLATFORM_DATABASE_URL or DATABASE_URL.
    """

    database_url: str
    retention_days: int
    enabled: bool

    @classmethod
    def from_env(cls) -> "MCPLogConfig":
        # Priority: MCP_LOG_DATABASE_URL > PLATFORM_DATABASE_URL > DATABASE_URL > local Postgres defaults
        database_url = env_optional_str("MCP_LOG_DATABASE_URL")
        if not database_url:
            database_url = env_optional_str("PLATFORM_DATABASE_URL")
        if not database_url:
            database_url = env_optional_str("DATABASE_URL")
        if not database_url:
            user = os.environ.get("POSTGRES_USER", "bsw")
            password = os.environ.get("POSTGRES_PASSWORD", "bsw")
            host = os.environ.get("POSTGRES_HOST", "postgres")
            port = os.environ.get("POSTGRES_PORT", "5432")
            name = os.environ.get("POSTGRES_DB", "bsw")
            database_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"

        return cls(
            database_url=database_url,
            retention_days=env_int("MCP_LOG_RETENTION_DAYS", 30),
            enabled=env_str("MCP_LOG_ENABLED", "true").lower() in ("true", "1", "yes"),
        )


# Global config instance
_config: Optional[MCPLogConfig] = None


def get_config() -> MCPLogConfig:
    """Get the MCP log configuration (cached)."""
    global _config
    if _config is None:
        _config = MCPLogConfig.from_env()
    return _config
