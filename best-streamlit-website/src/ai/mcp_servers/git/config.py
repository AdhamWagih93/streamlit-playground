from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from src.config_utils import env_int, env_optional_str, env_str


@dataclass(frozen=True)
class GitMCPServerConfig:
    """Runtime configuration for the Git MCP server.

    Git configuration:
    - GIT_REPO_PATH: default path to git repository (optional, can be specified per-call)
    - GIT_DEFAULT_BRANCH: default branch name (e.g., main, master)
    - GIT_TIMEOUT_SECONDS: command timeout (default: 60)

    MCP transport selection:
    - GIT_MCP_TRANSPORT: stdio|http|sse
    - GIT_MCP_HOST
    - GIT_MCP_PORT
    - GIT_MCP_URL: URL used by remote clients (when transport != stdio)

    Notes:
    - This server uses the git CLI directly (requires git to be installed).
    - All operations are read-only by default; write operations require explicit flags.
    """

    repo_path: Optional[str]
    default_branch: str
    timeout_seconds: int

    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_MCP_TRANSPORT: str = "http"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8006
    DEFAULT_MCP_URL: str = "http://git-mcp:8006"

    @classmethod
    def from_env(cls) -> "GitMCPServerConfig":
        transport = env_str("GIT_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()

        return cls(
            repo_path=env_optional_str("GIT_REPO_PATH"),
            default_branch=env_str("GIT_DEFAULT_BRANCH", "main"),
            timeout_seconds=env_int("GIT_TIMEOUT_SECONDS", 60),
            mcp_transport=transport,
            mcp_host=env_str("GIT_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("GIT_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("GIT_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_env_overrides(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        if self.repo_path:
            env["GIT_REPO_PATH"] = self.repo_path
        env["GIT_DEFAULT_BRANCH"] = self.default_branch
        env["GIT_TIMEOUT_SECONDS"] = str(self.timeout_seconds)

        env["MCP_TRANSPORT"] = self.mcp_transport
        env["MCP_HOST"] = self.mcp_host
        env["MCP_PORT"] = str(self.mcp_port)
        return env
