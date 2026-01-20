from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from src.config_utils import env_bool, env_int, env_optional_str, env_str


@dataclass(frozen=True)
class JenkinsMCPServerConfig:
    """Runtime configuration for the Jenkins MCP server.

    Reads from environment variables first; falls back to local-dev defaults.

    Env vars:
    - JENKINS_BASE_URL
    - JENKINS_USERNAME
    - JENKINS_API_TOKEN
    - JENKINS_VERIFY_SSL
    - JENKINS_MCP_CLIENT_TOKEN

    MCP transport selection:
    - JENKINS_MCP_TRANSPORT: stdio|http|sse (http is treated as sse)
    - JENKINS_MCP_HOST
    - JENKINS_MCP_PORT
    - JENKINS_MCP_URL: URL used by remote clients (when transport != stdio)
    """

    base_url: str
    username: Optional[str]
    api_token: Optional[str]
    verify_ssl: bool
    mcp_client_token: str
    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_BASE_URL: str = "http://localhost:8080"
    DEFAULT_VERIFY_SSL: bool = True
    DEFAULT_DEV_CLIENT_TOKEN: str = "dev-jenkins-mcp-token"
    DEFAULT_MCP_TRANSPORT: str = "stdio"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8000
    DEFAULT_MCP_URL: str = "http://jenkins-mcp:8000/sse"

    @classmethod
    def from_env(cls) -> "JenkinsMCPServerConfig":
        transport_raw = env_str("JENKINS_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()
        transport = "sse" if transport_raw == "http" else transport_raw
        return cls(
            base_url=env_str("JENKINS_BASE_URL", cls.DEFAULT_BASE_URL),
            username=env_optional_str("JENKINS_USERNAME"),
            api_token=env_optional_str("JENKINS_API_TOKEN"),
            verify_ssl=env_bool("JENKINS_VERIFY_SSL", cls.DEFAULT_VERIFY_SSL),
            mcp_client_token=env_str("JENKINS_MCP_CLIENT_TOKEN", cls.DEFAULT_DEV_CLIENT_TOKEN),
            mcp_transport=transport,
            mcp_host=env_str("JENKINS_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("JENKINS_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("JENKINS_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_env_overrides(self) -> Dict[str, str]:
        """Environment overrides suitable for launching the server as a subprocess."""

        return {
            "JENKINS_BASE_URL": self.base_url,
            "JENKINS_VERIFY_SSL": "true" if self.verify_ssl else "false",
            "JENKINS_MCP_CLIENT_TOKEN": self.mcp_client_token,
            "MCP_TRANSPORT": self.mcp_transport,
            "MCP_HOST": self.mcp_host,
            "MCP_PORT": str(self.mcp_port),
        }
