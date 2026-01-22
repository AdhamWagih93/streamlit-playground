from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.config_utils import env_optional_str, env_str


@dataclass(frozen=True)
class HelmToolConfig:
    """Runtime configuration for Helm *tools* exposed by kubernetes-mcp.

    There is no standalone Helm MCP server in this repo; Helm is just a set of
    tools hosted on the Kubernetes MCP server.

    Cluster selection (kubeconfig/context) is inherited from Kubernetes MCP.
    Helm itself should not have cluster-specific configuration knobs.
    """

    helm_bin: str
    auto_install: bool
    auto_install_version: str
    auto_install_dir: Optional[str]
    allow_raw: bool

    DEFAULT_HELM_BIN: str = "helm"
    DEFAULT_AUTO_INSTALL: bool = True
    DEFAULT_AUTO_INSTALL_VERSION: str = "v3.14.4"

    @classmethod
    def from_env(cls) -> "HelmToolConfig":
        allow_raw_raw = env_str("HELM_ALLOW_RAW", "false").lower().strip()
        allow_raw = allow_raw_raw in {"1", "true", "yes", "y", "on"}

        auto_install_raw = env_str("HELM_AUTO_INSTALL", "true").lower().strip()
        auto_install = auto_install_raw in {"1", "true", "yes", "y", "on"}
        auto_install_version = env_str("HELM_AUTO_INSTALL_VERSION", cls.DEFAULT_AUTO_INSTALL_VERSION).strip()
        auto_install_dir = env_optional_str("HELM_AUTO_INSTALL_DIR")

        return cls(
            helm_bin=env_str("HELM_BIN", cls.DEFAULT_HELM_BIN),
            auto_install=auto_install,
            auto_install_version=auto_install_version,
            auto_install_dir=auto_install_dir,
            allow_raw=allow_raw,
        )


__all__ = ["HelmToolConfig"]
