from __future__ import annotations

import os
import sys
from typing import Dict

from src.streamlit_config import StreamlitAppConfig
from src.ai.mcp_sdk_client import MCPServerSpec
from src.admin_config import load_admin_config


def build_server_specs(cfg: StreamlitAppConfig) -> Dict[str, MCPServerSpec]:
    """Build MCP server connection specs for Streamlit pages.

    - Uses the same env-first config as the rest of the app.
    - For tool auth, most servers in this repo accept `_client_token` as an arg.
    """

    specs = {
        "jenkins": MCPServerSpec(
            server_name="jenkins",
            transport=(cfg.jenkins.mcp_transport or "stdio").lower().strip(),
            module="src.ai.mcp_servers.jenkins.mcp",
            python_executable=sys.executable,
            env=cfg.build_jenkins_mcp_subprocess_env(dict(os.environ)),
            url=cfg.jenkins.mcp_url,
            client_token=cfg.jenkins.mcp_client_token,
        ),
        "kubernetes": MCPServerSpec(
            server_name="kubernetes",
            transport=(cfg.kubernetes.mcp_transport or "stdio").lower().strip(),
            module="src.ai.mcp_servers.kubernetes.mcp",
            python_executable=sys.executable,
            env=cfg.build_kubernetes_mcp_subprocess_env(dict(os.environ)),
            url=cfg.kubernetes.mcp_url,
            # Kubernetes MCP auth is env-driven in this repo.
            client_token=os.environ.get("KUBERNETES_MCP_CLIENT_TOKEN"),
        ),
        "docker": MCPServerSpec(
            server_name="docker",
            transport=(cfg.docker.mcp_transport or "stdio").lower().strip(),
            module="src.ai.mcp_servers.docker.mcp",
            python_executable=sys.executable,
            env=cfg.build_docker_mcp_subprocess_env(dict(os.environ)),
            url=cfg.docker.mcp_url,
            client_token=None,
        ),
        "nexus": MCPServerSpec(
            server_name="nexus",
            transport=(cfg.nexus.mcp_transport or "stdio").lower().strip(),
            module="src.ai.mcp_servers.nexus.mcp",
            python_executable=sys.executable,
            env=cfg.build_nexus_mcp_subprocess_env(dict(os.environ)),
            url=cfg.nexus.mcp_url,
            client_token=cfg.nexus.mcp_client_token,
        ),

        "scheduler": MCPServerSpec(
            server_name="scheduler",
            transport=(cfg.scheduler.mcp_transport or "http").lower().strip(),
            module="src.scheduler.main",
            python_executable=sys.executable,
            env=dict(os.environ),
            url=cfg.scheduler.mcp_url,
            client_token=None,
        ),

    }

    admin = load_admin_config()
    return {k: v for k, v in specs.items() if admin.is_mcp_enabled(k, default=True)}
