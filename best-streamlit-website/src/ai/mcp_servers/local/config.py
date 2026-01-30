from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from src.config_utils import env_bool, env_int, env_str


@dataclass(frozen=True)
class LocalMCPServerConfig:
    """Runtime configuration for the Local MCP server.

    File system scope:
    - LOCAL_MCP_ROOT: root directory for all operations (default: repo root)
    - LOCAL_MCP_ALLOW_WRITE: allow write/modify operations (default: true)

    MCP transport selection:
    - LOCAL_MCP_TRANSPORT: stdio|http|sse
    - LOCAL_MCP_HOST
    - LOCAL_MCP_PORT
    - LOCAL_MCP_URL: URL used by remote clients (when transport != stdio)
    """

    root_path: str
    allow_write: bool

    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_MCP_TRANSPORT: str = "http"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8000
    DEFAULT_MCP_URL: str = "http://local-mcp:8000"

    @staticmethod
    def _default_root() -> str:
        repo_root = Path(__file__).resolve().parents[4]
        return str(repo_root)

    @classmethod
    def from_env(cls) -> "LocalMCPServerConfig":
        transport = env_str("LOCAL_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()

        return cls(
            root_path=env_str("LOCAL_MCP_ROOT", cls._default_root()),
            allow_write=env_bool("LOCAL_MCP_ALLOW_WRITE", True),
            mcp_transport=transport,
            mcp_host=env_str("LOCAL_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("LOCAL_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("LOCAL_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_env_overrides(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        env["LOCAL_MCP_ROOT"] = str(self.root_path)
        env["LOCAL_MCP_ALLOW_WRITE"] = "true" if self.allow_write else "false"
        env["MCP_TRANSPORT"] = self.mcp_transport
        env["MCP_HOST"] = self.mcp_host
        env["MCP_PORT"] = str(self.mcp_port)
        return env
