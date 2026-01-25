from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from src.config_utils import env_bool, env_int, env_optional_str, env_str


@dataclass(frozen=True)
class TrivyMCPServerConfig:
    """Runtime configuration for the Trivy MCP server.

    Trivy configuration:
    - TRIVY_CACHE_DIR: directory for Trivy's vulnerability database cache
    - TRIVY_TIMEOUT_SECONDS: scan timeout (default: 300)
    - TRIVY_SEVERITY: comma-separated list of severities to report (default: CRITICAL,HIGH,MEDIUM,LOW)
    - TRIVY_IGNORE_UNFIXED: ignore unfixed vulnerabilities (default: False)
    - TRIVY_SKIP_DB_UPDATE: skip database update before scan (default: False)

    MCP transport selection:
    - TRIVY_MCP_TRANSPORT: stdio|http|sse
    - TRIVY_MCP_HOST
    - TRIVY_MCP_PORT
    - TRIVY_MCP_URL: URL used by remote clients (when transport != stdio)

    Notes:
    - This server uses the trivy CLI directly (requires trivy to be installed).
    - Trivy can scan container images, filesystems, git repositories, and more.
    """

    cache_dir: Optional[str]
    timeout_seconds: int
    severity: str
    ignore_unfixed: bool
    skip_db_update: bool

    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_MCP_TRANSPORT: str = "http"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8007
    DEFAULT_MCP_URL: str = "http://trivy-mcp:8007"

    @classmethod
    def from_env(cls) -> "TrivyMCPServerConfig":
        transport = env_str("TRIVY_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()

        return cls(
            cache_dir=env_optional_str("TRIVY_CACHE_DIR"),
            timeout_seconds=env_int("TRIVY_TIMEOUT_SECONDS", 300),
            severity=env_str("TRIVY_SEVERITY", "CRITICAL,HIGH,MEDIUM,LOW"),
            ignore_unfixed=env_bool("TRIVY_IGNORE_UNFIXED", False),
            skip_db_update=env_bool("TRIVY_SKIP_DB_UPDATE", False),
            mcp_transport=transport,
            mcp_host=env_str("TRIVY_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("TRIVY_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("TRIVY_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_env_overrides(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        if self.cache_dir:
            env["TRIVY_CACHE_DIR"] = self.cache_dir
        env["TRIVY_TIMEOUT_SECONDS"] = str(self.timeout_seconds)
        env["TRIVY_SEVERITY"] = self.severity
        if self.ignore_unfixed:
            env["TRIVY_IGNORE_UNFIXED"] = "1"
        if self.skip_db_update:
            env["TRIVY_SKIP_DB_UPDATE"] = "1"

        env["MCP_TRANSPORT"] = self.mcp_transport
        env["MCP_HOST"] = self.mcp_host
        env["MCP_PORT"] = str(self.mcp_port)
        return env
