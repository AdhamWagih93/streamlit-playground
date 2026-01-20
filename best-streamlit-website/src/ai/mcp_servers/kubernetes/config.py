from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from src.config_utils import env_int, env_optional_str, env_str


@dataclass(frozen=True)
class KubernetesMCPServerConfig:
    """Runtime configuration for the Kubernetes MCP server.

    Env vars:
    - K8S_KUBECONFIG: path to kubeconfig file
    - K8S_CONTEXT: kube context name

    If unset, the Kubernetes client falls back to in-cluster config or default
    kubeconfig loading rules.

    MCP transport selection:
    - KUBERNETES_MCP_TRANSPORT: stdio|http|sse (http is treated as sse)
    - KUBERNETES_MCP_HOST
    - KUBERNETES_MCP_PORT
    - KUBERNETES_MCP_URL: URL used by remote clients (when transport != stdio)
    """

    kubeconfig: Optional[str]
    context: Optional[str]
    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_MCP_TRANSPORT: str = "stdio"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8000
    DEFAULT_MCP_URL: str = "http://kubernetes-mcp:8000/sse"

    @classmethod
    def from_env(cls) -> "KubernetesMCPServerConfig":
        transport_raw = env_str("KUBERNETES_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()
        transport = "sse" if transport_raw == "http" else transport_raw
        return cls(
            kubeconfig=env_optional_str("K8S_KUBECONFIG"),
            context=env_optional_str("K8S_CONTEXT"),
            mcp_transport=transport,
            mcp_host=env_str("KUBERNETES_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("KUBERNETES_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("KUBERNETES_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_env_overrides(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        if self.kubeconfig:
            env["K8S_KUBECONFIG"] = self.kubeconfig
        if self.context:
            env["K8S_CONTEXT"] = self.context
        env["MCP_TRANSPORT"] = self.mcp_transport
        env["MCP_HOST"] = self.mcp_host
        env["MCP_PORT"] = str(self.mcp_port)
        return env
