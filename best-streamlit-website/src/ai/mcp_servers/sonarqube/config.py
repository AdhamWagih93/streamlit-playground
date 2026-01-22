from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.config_utils import env_bool, env_int, env_optional_str, env_str


@dataclass(frozen=True)
class SonarQubeMCPServerConfig:
    """Runtime configuration for the SonarQube MCP server.

    Reads from environment variables first; falls back to local-dev defaults.

    Env vars:
    - SONARQUBE_BASE_URL
    - SONARQUBE_TOKEN (authentication token)
    - SONARQUBE_USERNAME (optional, for user/password auth)
    - SONARQUBE_PASSWORD (optional, for user/password auth)
    - SONARQUBE_VERIFY_SSL
    - SONARQUBE_MCP_CLIENT_TOKEN

    MCP transport selection:
    - SONARQUBE_MCP_TRANSPORT: stdio|http|sse
    - SONARQUBE_MCP_HOST
    - SONARQUBE_MCP_PORT
    - SONARQUBE_MCP_URL: URL used by remote clients (when transport != stdio)
    """

    base_url: str
    token: Optional[str]
    username: Optional[str]
    password: Optional[str]
    verify_ssl: bool
    mcp_client_token: str
    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_BASE_URL: str = "http://localhost:9000"
    DEFAULT_VERIFY_SSL: bool = True
    DEFAULT_DEV_CLIENT_TOKEN: str = "dev-sonarqube-mcp-token"
    DEFAULT_MCP_TRANSPORT: str = "stdio"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8002
    DEFAULT_MCP_URL: str = "http://sonarqube-mcp:8002"

    @classmethod
    def from_env(cls) -> "SonarQubeMCPServerConfig":
        transport = env_str("SONARQUBE_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()
        return cls(
            base_url=env_str("SONARQUBE_BASE_URL", cls.DEFAULT_BASE_URL),
            token=env_optional_str("SONARQUBE_TOKEN"),
            username=env_optional_str("SONARQUBE_USERNAME"),
            password=env_optional_str("SONARQUBE_PASSWORD"),
            verify_ssl=env_bool("SONARQUBE_VERIFY_SSL", cls.DEFAULT_VERIFY_SSL),
            mcp_client_token=env_str("SONARQUBE_MCP_CLIENT_TOKEN", cls.DEFAULT_DEV_CLIENT_TOKEN),
            mcp_transport=transport,
            mcp_host=env_str("SONARQUBE_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("SONARQUBE_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("SONARQUBE_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "has_token": bool(self.token),
            "has_username": bool(self.username),
            "verify_ssl": self.verify_ssl,
            "mcp_transport": self.mcp_transport,
            "mcp_host": self.mcp_host,
            "mcp_port": self.mcp_port,
            "mcp_url": self.mcp_url,
        }
