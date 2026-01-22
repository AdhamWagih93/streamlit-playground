from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from src.ai.mcp_servers.jenkins.config import JenkinsMCPServerConfig
from src.ai.mcp_servers.kubernetes.config import KubernetesMCPServerConfig
from src.ai.mcp_servers.docker.config import DockerMCPServerConfig
from src.ai.mcp_servers.nexus.config import NexusMCPServerConfig
from src.config_utils import env_str


@dataclass(frozen=True)
class StreamlitAppConfig:
    """Streamlit app configuration.

    This is UI/runtime configuration (not Streamlit's config.toml).
    Env-first with safe local-dev defaults.

    Notes:
    - Jenkins MCP tool calls require the same token configured on the Jenkins
      MCP server. For local dev, both default to the same value.
    """

    jenkins: JenkinsMCPServerConfig
    kubernetes: KubernetesMCPServerConfig
    docker: DockerMCPServerConfig
    nexus: NexusMCPServerConfig

    @classmethod
    def from_env(cls) -> "StreamlitAppConfig":
        # Allow Streamlit to override the Jenkins token independently if desired,
        # while still defaulting to the same dev token as the server config.
        jenkins = JenkinsMCPServerConfig.from_env()
        streamlit_token = env_str("STREAMLIT_JENKINS_MCP_CLIENT_TOKEN", jenkins.mcp_client_token)
        jenkins = JenkinsMCPServerConfig(
            base_url=jenkins.base_url,
            username=jenkins.username,
            api_token=jenkins.api_token,
            verify_ssl=jenkins.verify_ssl,
            mcp_client_token=streamlit_token,
            mcp_transport=env_str("STREAMLIT_JENKINS_MCP_TRANSPORT", jenkins.mcp_transport),
            mcp_host=jenkins.mcp_host,
            mcp_port=jenkins.mcp_port,
            mcp_url=env_str("STREAMLIT_JENKINS_MCP_URL", jenkins.mcp_url),
        )

        kubernetes = KubernetesMCPServerConfig.from_env()
        kubernetes = KubernetesMCPServerConfig(
            kubeconfig=kubernetes.kubeconfig,
            context=kubernetes.context,
            mcp_transport=env_str("STREAMLIT_KUBERNETES_MCP_TRANSPORT", kubernetes.mcp_transport),
            mcp_host=kubernetes.mcp_host,
            mcp_port=kubernetes.mcp_port,
            mcp_url=env_str("STREAMLIT_KUBERNETES_MCP_URL", kubernetes.mcp_url),
        )

        docker = DockerMCPServerConfig.from_env()
        docker = DockerMCPServerConfig(
            docker_host=docker.docker_host,
            docker_tls_verify=docker.docker_tls_verify,
            docker_cert_path=docker.docker_cert_path,
            docker_timeout_seconds=docker.docker_timeout_seconds,
            mcp_transport=env_str("STREAMLIT_DOCKER_MCP_TRANSPORT", docker.mcp_transport),
            mcp_host=docker.mcp_host,
            mcp_port=docker.mcp_port,
            mcp_url=env_str("STREAMLIT_DOCKER_MCP_URL", docker.mcp_url),
        )

        nexus = NexusMCPServerConfig.from_env()
        nexus = NexusMCPServerConfig(
            base_url=nexus.base_url,
            username=nexus.username,
            password=nexus.password,
            token=nexus.token,
            verify_ssl=nexus.verify_ssl,
            mcp_client_token=env_str("STREAMLIT_NEXUS_MCP_CLIENT_TOKEN", nexus.mcp_client_token or "") or None,
            allow_raw=nexus.allow_raw,
            mcp_transport=env_str("STREAMLIT_NEXUS_MCP_TRANSPORT", nexus.mcp_transport),
            mcp_host=nexus.mcp_host,
            mcp_port=nexus.mcp_port,
            mcp_url=env_str("STREAMLIT_NEXUS_MCP_URL", nexus.mcp_url),
        )

        return cls(jenkins=jenkins, kubernetes=kubernetes, docker=docker, nexus=nexus)

    def build_jenkins_mcp_subprocess_env(self, base_env: Dict[str, str]) -> Dict[str, str]:
        return {**base_env, **self.jenkins.to_env_overrides()}

    def build_kubernetes_mcp_subprocess_env(self, base_env: Dict[str, str]) -> Dict[str, str]:
        return {**base_env, **self.kubernetes.to_env_overrides()}

    def build_docker_mcp_subprocess_env(self, base_env: Dict[str, str]) -> Dict[str, str]:
        return {**base_env, **self.docker.to_env_overrides()}

    def build_nexus_mcp_subprocess_env(self, base_env: Dict[str, str]) -> Dict[str, str]:
        return {**base_env, **self.nexus.to_env_overrides()}
