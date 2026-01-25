from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from src.config_utils import env_bool, env_int, env_optional_str, env_str


@dataclass(frozen=True)
class NexusMCPServerConfig:
    """Runtime configuration for the Nexus MCP server.

    Defaults target a local Nexus Repository Manager instance.

    Env vars:
    - NEXUS_BASE_URL (default http://localhost:8081)
    - NEXUS_USERNAME / NEXUS_PASSWORD (optional basic auth)
    - NEXUS_TOKEN (optional bearer token)
    - NEXUS_VERIFY_SSL (default true)

    MCP auth:
    - NEXUS_MCP_CLIENT_TOKEN (optional; when set, tools require _client_token)

    MCP transport:
    - NEXUS_MCP_TRANSPORT: stdio|http|sse
    - NEXUS_MCP_HOST
    - NEXUS_MCP_PORT
    - NEXUS_MCP_URL

    Safety:
    - NEXUS_ALLOW_RAW (default false) enables the generic raw request tool.
    """

    base_url: str
    username: Optional[str]
    password: Optional[str]
    token: Optional[str]
    verify_ssl: bool

    mcp_client_token: Optional[str]
    allow_raw: bool

    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_BASE_URL: str = "http://localhost:8081"
    DEFAULT_VERIFY_SSL: bool = True

    DEFAULT_MCP_TRANSPORT: str = "http"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8003
    DEFAULT_MCP_URL: str = "http://nexus-mcp:8003"

    @classmethod
    def from_env(cls) -> "NexusMCPServerConfig":
        transport = env_str("NEXUS_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()

        allow_raw_raw = env_str("NEXUS_ALLOW_RAW", "false").lower().strip()
        allow_raw = allow_raw_raw in {"1", "true", "yes", "y", "on"}

        return cls(
            base_url=env_str("NEXUS_BASE_URL", cls.DEFAULT_BASE_URL).rstrip("/"),
            username=env_optional_str("NEXUS_USERNAME"),
            password=env_optional_str("NEXUS_PASSWORD"),
            token=env_optional_str("NEXUS_TOKEN"),
            verify_ssl=env_bool("NEXUS_VERIFY_SSL", cls.DEFAULT_VERIFY_SSL),
            mcp_client_token=env_optional_str("NEXUS_MCP_CLIENT_TOKEN"),
            allow_raw=allow_raw,
            mcp_transport=transport,
            mcp_host=env_str("NEXUS_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("NEXUS_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("NEXUS_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_env_overrides(self) -> Dict[str, str]:
        env: Dict[str, str] = {
            "NEXUS_BASE_URL": self.base_url,
            "NEXUS_VERIFY_SSL": "true" if self.verify_ssl else "false",
            "NEXUS_ALLOW_RAW": "true" if self.allow_raw else "false",
            "MCP_TRANSPORT": self.mcp_transport,
            "MCP_HOST": self.mcp_host,
            "MCP_PORT": str(self.mcp_port),
        }
        if self.username:
            env["NEXUS_USERNAME"] = self.username
        if self.password:
            env["NEXUS_PASSWORD"] = self.password
        if self.token:
            env["NEXUS_TOKEN"] = self.token
        if self.mcp_client_token:
            env["NEXUS_MCP_CLIENT_TOKEN"] = self.mcp_client_token
        return env

    def to_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "has_username": bool(self.username),
            "has_password": bool(self.password),
            "has_token": bool(self.token),
            "verify_ssl": self.verify_ssl,
            "allow_raw": self.allow_raw,
            "mcp_transport": self.mcp_transport,
            "mcp_host": self.mcp_host,
            "mcp_port": self.mcp_port,
            "mcp_url": self.mcp_url,
        }
