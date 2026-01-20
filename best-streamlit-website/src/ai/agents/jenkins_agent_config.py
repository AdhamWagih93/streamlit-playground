from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from src.ai.agents.tool_agent_types import ToolAgentConfig
from src.ai.mcp_servers.jenkins.config import JenkinsMCPServerConfig


@dataclass(frozen=True)
class JenkinsToolAgentConfig:
    """Config for the Jenkins tool-using agent."""

    tool_agent: ToolAgentConfig
    mcp_client_token: str

    @classmethod
    def from_env(cls) -> "JenkinsToolAgentConfig":
        jenkins_cfg = JenkinsMCPServerConfig.from_env()

        # Agent (client) must send the same token expected by the MCP server.
        token = jenkins_cfg.mcp_client_token

        env_overrides: Dict[str, str] = {
            # runtime connection settings for the server
            **jenkins_cfg.to_env_overrides(),
        }

        tool_agent = ToolAgentConfig.from_env(
            agent_name="jenkins_agent",
            mcp_server_name="jenkins",
            mcp_module="src.ai.mcp_servers.jenkins.mcp",
            default_env=env_overrides,
            remote_url_env="JENKINS_MCP_URL",
            transport_env="JENKINS_MCP_TRANSPORT",
            default_remote_url=jenkins_cfg.mcp_url,
        )

        return cls(tool_agent=tool_agent, mcp_client_token=token)
