from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.ai.mcp_sdk_client import MCPServerSpec
from src.ai.mcp_servers.docker.config import DockerMCPServerConfig
from src.ai.mcp_servers.jenkins.config import JenkinsMCPServerConfig
from src.ai.mcp_servers.kubernetes.config import KubernetesMCPServerConfig
from src.ai.mcp_servers.nexus.config import NexusMCPServerConfig


def build_target_specs() -> Dict[str, MCPServerSpec]:
    """Build MCP server specs for the scheduler runtime.

    The scheduler executes jobs by calling MCP tools on other servers.

    Configuration sources:
    - Uses the same env vars as the MCP server configs (JENKINS_MCP_URL, etc.)
    - For stdio mode, launches the server modules as subprocesses.

    Note: MultiServerMCPClient expects "sse" for HTTP transports.
    """

    j = JenkinsMCPServerConfig.from_env()
    k = KubernetesMCPServerConfig.from_env()
    d = DockerMCPServerConfig.from_env()
    n = NexusMCPServerConfig.from_env()

    def _t(x: str) -> str:
        t = (x or "").lower().strip()
        return "sse" if t == "http" else (t or "stdio")

    specs: Dict[str, MCPServerSpec] = {
        "jenkins": MCPServerSpec(
            server_name="jenkins",
            transport=_t(j.mcp_transport),
            module="src.ai.mcp_servers.jenkins.mcp",
            python_executable=sys.executable,
            env={**os.environ, **j.to_env_overrides()},
            url=j.mcp_url,
            client_token=j.mcp_client_token,
        ),
        "kubernetes": MCPServerSpec(
            server_name="kubernetes",
            transport=_t(k.mcp_transport),
            module="src.ai.mcp_servers.kubernetes.mcp",
            python_executable=sys.executable,
            env={**os.environ, **k.to_env_overrides()},
            url=k.mcp_url,
            client_token=os.environ.get("KUBERNETES_MCP_CLIENT_TOKEN"),
        ),
        "docker": MCPServerSpec(
            server_name="docker",
            transport=_t(d.mcp_transport),
            module="src.ai.mcp_servers.docker.mcp",
            python_executable=sys.executable,
            env={**os.environ, **d.to_env_overrides()},
            url=d.mcp_url,
            client_token=None,
        ),
        "nexus": MCPServerSpec(
            server_name="nexus",
            transport=_t(n.mcp_transport),
            module="src.ai.mcp_servers.nexus.mcp",
            python_executable=sys.executable,
            env={**os.environ, **n.to_env_overrides()},
            url=n.mcp_url,
            client_token=n.mcp_client_token,
        ),
    }

    return specs


def build_langchain_conn(spec: MCPServerSpec) -> Dict[str, Any]:
    transport = (spec.transport or "stdio").lower().strip()

    if transport == "stdio":
        env = {**os.environ, **dict(spec.env or {})}
        # Ensure stdio subprocess can import this workspace's src package.
        # .../best-streamlit-website/src/scheduler/mcp_targets.py -> repo root
        repo_root = str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = repo_root + (os.pathsep + existing_pp if existing_pp else "")
        return {
            "transport": "stdio",
            "command": spec.python_executable or os.environ.get("PYTHON") or "python",
            "args": ["-m", str(spec.module or "")],
            "env": env,
        }

    # MultiServerMCPClient uses sse for http.
    return {"transport": transport, "url": str(spec.url or "")}
