from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from src.config_utils import env_int, env_optional_str, env_str


@dataclass(frozen=True)
class HelmMCPServerConfig:
    """Runtime configuration for the Helm MCP server.

    Helm execution:
    - HELM_BIN: path to helm binary (default: helm)
    - HELM_AUTO_INSTALL: if true (default), auto-download Helm 3 when HELM_BIN is not found
    - HELM_AUTO_INSTALL_VERSION: Helm version to download (default: v3.14.4)
    - HELM_AUTO_INSTALL_DIR: optional directory to store the downloaded helm binary
    - HELM_KUBECONFIG: optional kubeconfig path (falls back to K8S_KUBECONFIG)
    - HELM_KUBECONTEXT: optional kube context (falls back to K8S_CONTEXT)

    MCP transport selection:
    - HELM_MCP_TRANSPORT: stdio|http|sse (http is treated as sse)
    - HELM_MCP_HOST
    - HELM_MCP_PORT
    - HELM_MCP_URL: URL used by remote clients (when transport != stdio)

    Security:
    - HELM_ALLOW_RAW: if true, enables the `helm_raw` tool for arbitrary helm args
    """

    helm_bin: str
    auto_install: bool
    auto_install_version: str
    auto_install_dir: Optional[str]
    kubeconfig: Optional[str]
    kubecontext: Optional[str]
    allow_raw: bool

    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_HELM_BIN: str = "helm"
    DEFAULT_AUTO_INSTALL: bool = True
    DEFAULT_AUTO_INSTALL_VERSION: str = "v3.14.4"

    DEFAULT_MCP_TRANSPORT: str = "stdio"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8000
    DEFAULT_MCP_URL: str = "http://helm-mcp:8000/sse"

    @classmethod
    def from_env(cls) -> "HelmMCPServerConfig":
        transport_raw = env_str("HELM_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()
        transport = "sse" if transport_raw == "http" else transport_raw

        allow_raw_raw = env_str("HELM_ALLOW_RAW", "false").lower().strip()
        allow_raw = allow_raw_raw in {"1", "true", "yes", "y", "on"}

        auto_install_raw = env_str("HELM_AUTO_INSTALL", "true").lower().strip()
        auto_install = auto_install_raw in {"1", "true", "yes", "y", "on"}
        auto_install_version = env_str("HELM_AUTO_INSTALL_VERSION", cls.DEFAULT_AUTO_INSTALL_VERSION).strip()
        auto_install_dir = env_optional_str("HELM_AUTO_INSTALL_DIR")

        kubeconfig = env_optional_str("HELM_KUBECONFIG") or env_optional_str("K8S_KUBECONFIG")
        kubecontext = env_optional_str("HELM_KUBECONTEXT") or env_optional_str("K8S_CONTEXT")

        return cls(
            helm_bin=env_str("HELM_BIN", cls.DEFAULT_HELM_BIN),
            auto_install=auto_install,
            auto_install_version=auto_install_version,
            auto_install_dir=auto_install_dir,
            kubeconfig=kubeconfig,
            kubecontext=kubecontext,
            allow_raw=allow_raw,
            mcp_transport=transport,
            mcp_host=env_str("HELM_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("HELM_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("HELM_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_env_overrides(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        if self.kubeconfig:
            env["K8S_KUBECONFIG"] = self.kubeconfig
        if self.kubecontext:
            env["K8S_CONTEXT"] = self.kubecontext
        env["HELM_BIN"] = self.helm_bin
        env["HELM_AUTO_INSTALL"] = "true" if self.auto_install else "false"
        env["HELM_AUTO_INSTALL_VERSION"] = self.auto_install_version
        if self.auto_install_dir:
            env["HELM_AUTO_INSTALL_DIR"] = self.auto_install_dir
        env["HELM_ALLOW_RAW"] = "true" if self.allow_raw else "false"

        env["MCP_TRANSPORT"] = self.mcp_transport
        env["MCP_HOST"] = self.mcp_host
        env["MCP_PORT"] = str(self.mcp_port)
        return env
