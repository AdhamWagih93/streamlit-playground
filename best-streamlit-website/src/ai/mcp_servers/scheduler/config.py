from __future__ import annotations

from dataclasses import dataclass

from src.config_utils import env_str


@dataclass(frozen=True)
class SchedulerMCPServerConfig:
    """Config for connecting Streamlit (client) to the scheduler MCP service."""

    mcp_transport: str
    mcp_url: str

    # Optional: allow stdio launch in local dev, but default to http.
    mcp_host: str
    mcp_port: int

    @classmethod
    def from_env(cls) -> "SchedulerMCPServerConfig":
        return cls(
            mcp_transport=env_str("SCHEDULER_MCP_TRANSPORT", "http"),
            mcp_url=env_str("SCHEDULER_MCP_URL", "http://127.0.0.1:8010"),
            mcp_host=env_str("SCHEDULER_MCP_HOST", "127.0.0.1"),
            mcp_port=int(env_str("SCHEDULER_MCP_PORT", "8010")),
        )

    def to_env_overrides(self) -> dict[str, str]:
        return {
            "SCHEDULER_MCP_TRANSPORT": str(self.mcp_transport),
            "SCHEDULER_MCP_URL": str(self.mcp_url),
            "SCHEDULER_MCP_HOST": str(self.mcp_host),
            "SCHEDULER_MCP_PORT": str(self.mcp_port),
        }
