from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from src.ai.agents.tool_agent_types import ToolAgentConfig
from src.ai.mcp_servers.kubernetes.config import KubernetesMCPServerConfig


@dataclass(frozen=True)
class KubernetesToolAgentConfig:
    """Config for a Kubernetes tool-using agent (MCP tools)."""

    tool_agent: ToolAgentConfig

    @classmethod
    def from_env(cls) -> "KubernetesToolAgentConfig":
        k8s_cfg = KubernetesMCPServerConfig.from_env()

        env_overrides: Dict[str, str] = {
            **k8s_cfg.to_env_overrides(),
        }

        tool_agent = ToolAgentConfig.from_env(
            agent_name="kubernetes_agent",
            mcp_server_name="kubernetes",
            mcp_module="src.ai.mcp_servers.kubernetes.mcp",
            default_env=env_overrides,
            remote_url_env="KUBERNETES_MCP_URL",
            transport_env="KUBERNETES_MCP_TRANSPORT",
            default_remote_url=k8s_cfg.mcp_url,
        )

        return cls(tool_agent=tool_agent)
