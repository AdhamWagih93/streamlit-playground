from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.config_utils import env_bool, env_optional_str, env_str


@dataclass(frozen=True)
class LLMConfig:
    """LLM runtime config for agents.

    Defaults are local-dev friendly (Ollama on localhost).

    Env vars:
    - OLLAMA_BASE_URL
    - OLLAMA_MODEL
    - OLLAMA_TEMPERATURE
    """

    base_url: str
    model: str
    temperature: float

    DEFAULT_BASE_URL: str = "http://localhost:11434"
    DEFAULT_MODEL: str = "qwen2.5:7b-instruct-q6_K"
    DEFAULT_TEMPERATURE: float = 0.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        # temperature parsing kept simple to avoid extra helpers
        raw_temp = env_str("OLLAMA_TEMPERATURE", str(cls.DEFAULT_TEMPERATURE))
        try:
            temp = float(raw_temp)
        except Exception:
            temp = cls.DEFAULT_TEMPERATURE

        return cls(
            base_url=env_str("OLLAMA_BASE_URL", cls.DEFAULT_BASE_URL),
            model=env_str("OLLAMA_MODEL", cls.DEFAULT_MODEL),
            temperature=temp,
        )


@dataclass(frozen=True)
class MCPStdioModuleServerConfig:
    """How to launch an MCP server via `python -m <module>` over stdio."""

    server_name: str
    module: str
    env_overrides: Dict[str, str]

    def to_connection(self, *, python_executable: str) -> Dict[str, Any]:
        return {
            "transport": "stdio",
            "command": python_executable,
            "args": ["-m", self.module],
            "env": self.env_overrides,
        }


@dataclass(frozen=True)
class MCPRemoteServerConfig:
    """How to connect to an MCP server over HTTP/SSE."""

    server_name: str
    url: str

    def to_connection(self) -> Dict[str, Any]:
        # LangChain MCP adapters typically use SSE for network transport.
        # We accept `http` in configs but map it to SSE by providing this connection.
        return {
            "transport": "sse",
            "url": self.url,
        }


@dataclass(frozen=True)
class ToolAgentConfig:
    """Config for an agent that uses MCP-discovered tools."""

    agent_name: str
    llm: LLMConfig
    mcp_server: Any

    @classmethod
    def from_env(
        cls,
        *,
        agent_name: str,
        mcp_server_name: str,
        mcp_module: str,
        default_env: Dict[str, str],
        remote_url_env: str,
        transport_env: str,
        default_remote_url: str,
    ) -> "ToolAgentConfig":
        """Generic env-first loader for tool-agents.

        Allows per-agent override of env values by setting env vars prefixed with
        `<AGENT_NAME>_` (uppercased, non-alnum -> underscore).

        Example: JENKINS_AGENT_OLLAMA_MODEL, JENKINS_AGENT_OLLAMA_BASE_URL
        """

        prefix = "".join([c if c.isalnum() else "_" for c in agent_name.upper()]) + "_"

        base_url = env_str(prefix + "OLLAMA_BASE_URL", env_str("OLLAMA_BASE_URL", LLMConfig.DEFAULT_BASE_URL))
        model = env_str(prefix + "OLLAMA_MODEL", env_str("OLLAMA_MODEL", LLMConfig.DEFAULT_MODEL))

        raw_temp = env_str(prefix + "OLLAMA_TEMPERATURE", env_str("OLLAMA_TEMPERATURE", str(LLMConfig.DEFAULT_TEMPERATURE)))
        try:
            temp = float(raw_temp)
        except Exception:
            temp = LLMConfig.DEFAULT_TEMPERATURE

        llm = LLMConfig(base_url=base_url, model=model, temperature=temp)

        # MCP env overrides can also be prefixed per agent, but callers typically
        # supply `default_env` already containing env-first values.
        env_overrides = dict(default_env)

        # Optional generic toggle to pass through OS proxy vars.
        include_proxies = env_bool(prefix + "INCLUDE_PROXIES", True)
        if include_proxies:
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
                v = env_optional_str(k)
                if v and k not in env_overrides:
                    env_overrides[k] = v

        transport_raw = env_str(transport_env, "stdio").lower().strip()
        transport = "sse" if transport_raw == "http" else transport_raw

        if transport == "stdio":
            mcp = MCPStdioModuleServerConfig(server_name=mcp_server_name, module=mcp_module, env_overrides=env_overrides)
        else:
            url = env_str(remote_url_env, default_remote_url)
            mcp = MCPRemoteServerConfig(server_name=mcp_server_name, url=url)
        return cls(agent_name=agent_name, llm=llm, mcp_server=mcp)
