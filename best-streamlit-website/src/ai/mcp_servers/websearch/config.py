from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from src.config_utils import env_int, env_str


@dataclass(frozen=True)
class WebSearchMCPServerConfig:
    """Runtime configuration for the Web Search MCP server.

    Web Search configuration:
    - WEBSEARCH_MAX_RESULTS: maximum results per search (default: 10)
    - WEBSEARCH_REGION: search region code (default: wt-wt for global)
    - WEBSEARCH_SAFESEARCH: safe search level (off, moderate, strict)
    - WEBSEARCH_TIMEOUT_SECONDS: request timeout (default: 30)

    MCP transport selection:
    - WEBSEARCH_MCP_TRANSPORT: stdio|http|sse
    - WEBSEARCH_MCP_HOST
    - WEBSEARCH_MCP_PORT
    - WEBSEARCH_MCP_URL: URL used by remote clients (when transport != stdio)

    Notes:
    - Uses DuckDuckGo for web searches (no API key required).
    - Supports text search, news search, and image search.
    """

    max_results: int
    region: str
    safesearch: str
    timeout_seconds: int

    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_MCP_TRANSPORT: str = "http"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8009
    DEFAULT_MCP_URL: str = "http://websearch-mcp:8009"

    @classmethod
    def from_env(cls) -> "WebSearchMCPServerConfig":
        transport = env_str("WEBSEARCH_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()

        return cls(
            max_results=env_int("WEBSEARCH_MAX_RESULTS", 10),
            region=env_str("WEBSEARCH_REGION", "wt-wt"),
            safesearch=env_str("WEBSEARCH_SAFESEARCH", "moderate"),
            timeout_seconds=env_int("WEBSEARCH_TIMEOUT_SECONDS", 30),
            mcp_transport=transport,
            mcp_host=env_str("WEBSEARCH_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("WEBSEARCH_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("WEBSEARCH_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_env_overrides(self) -> Dict[str, str]:
        return {
            "WEBSEARCH_MAX_RESULTS": str(self.max_results),
            "WEBSEARCH_REGION": self.region,
            "WEBSEARCH_SAFESEARCH": self.safesearch,
            "WEBSEARCH_TIMEOUT_SECONDS": str(self.timeout_seconds),
            "MCP_TRANSPORT": self.mcp_transport,
            "MCP_HOST": self.mcp_host,
            "MCP_PORT": str(self.mcp_port),
        }
