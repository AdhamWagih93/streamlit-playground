from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from src.config_utils import env_bool, env_int, env_optional_str, env_str


@dataclass(frozen=True)
class DockerMCPServerConfig:
    """Runtime configuration for the Docker MCP server.

    Docker client:
    - DOCKER_HOST: override Docker daemon endpoint (e.g. tcp://host:2376)
    - DOCKER_TLS_VERIFY: enable TLS verification
    - DOCKER_CERT_PATH: directory containing ca.pem/cert.pem/key.pem
    - DOCKER_TIMEOUT_SECONDS: client timeout (default: 60)

    MCP transport selection:
    - DOCKER_MCP_TRANSPORT: stdio|http|sse
    - DOCKER_MCP_HOST
    - DOCKER_MCP_PORT
    - DOCKER_MCP_URL: URL used by remote clients (when transport != stdio)

    Notes:
    - This server uses the Docker Engine API via the Python docker SDK.
      It does not require the docker CLI.
    """

    docker_host: Optional[str]
    docker_tls_verify: bool
    docker_cert_path: Optional[str]
    docker_timeout_seconds: int

    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_MCP_TRANSPORT: str = "stdio"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8000
    DEFAULT_MCP_URL: str = "http://docker-mcp:8000"

    @classmethod
    def from_env(cls) -> "DockerMCPServerConfig":
        transport = env_str("DOCKER_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()

        return cls(
            docker_host=env_optional_str("DOCKER_HOST"),
            docker_tls_verify=env_bool("DOCKER_TLS_VERIFY", False),
            docker_cert_path=env_optional_str("DOCKER_CERT_PATH"),
            docker_timeout_seconds=env_int("DOCKER_TIMEOUT_SECONDS", 60),
            mcp_transport=transport,
            mcp_host=env_str("DOCKER_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("DOCKER_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("DOCKER_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_env_overrides(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        if self.docker_host:
            env["DOCKER_HOST"] = self.docker_host
        if self.docker_cert_path:
            env["DOCKER_CERT_PATH"] = self.docker_cert_path
        # docker-py treats the presence of DOCKER_TLS_VERIFY as enabling TLS
        # (it does not interpret "0" as false). Only set it when explicitly enabled.
        if self.docker_tls_verify:
            env["DOCKER_TLS_VERIFY"] = "1"
        env["DOCKER_TIMEOUT_SECONDS"] = str(self.docker_timeout_seconds)

        env["MCP_TRANSPORT"] = self.mcp_transport
        env["MCP_HOST"] = self.mcp_host
        env["MCP_PORT"] = str(self.mcp_port)
        return env
