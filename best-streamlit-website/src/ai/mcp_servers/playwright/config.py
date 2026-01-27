from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from src.config_utils import env_bool, env_int, env_optional_str, env_str


@dataclass(frozen=True)
class PlaywrightMCPServerConfig:
    """Runtime configuration for the Playwright MCP server.

    Playwright configuration:
    - PLAYWRIGHT_HEADLESS: run browser in headless mode (default: true)
    - PLAYWRIGHT_BROWSER: browser type (chromium, firefox, webkit) default: chromium
    - PLAYWRIGHT_TIMEOUT_MS: default timeout in milliseconds (default: 30000)
    - PLAYWRIGHT_SCREENSHOT_DIR: directory for screenshots (default: /tmp/playwright)

    MCP transport selection:
    - PLAYWRIGHT_MCP_TRANSPORT: stdio|http|sse
    - PLAYWRIGHT_MCP_HOST
    - PLAYWRIGHT_MCP_PORT
    - PLAYWRIGHT_MCP_URL: URL used by remote clients (when transport != stdio)

    Notes:
    - This server uses Playwright for browser automation.
    - Supports taking screenshots, navigating pages, extracting content, and more.
    """

    headless: bool
    browser: str
    timeout_ms: int
    screenshot_dir: str

    mcp_transport: str
    mcp_host: str
    mcp_port: int
    mcp_url: str

    DEFAULT_MCP_TRANSPORT: str = "http"
    DEFAULT_MCP_HOST: str = "0.0.0.0"
    DEFAULT_MCP_PORT: int = 8008
    DEFAULT_MCP_URL: str = "http://playwright-mcp:8008"

    @classmethod
    def from_env(cls) -> "PlaywrightMCPServerConfig":
        transport = env_str("PLAYWRIGHT_MCP_TRANSPORT", cls.DEFAULT_MCP_TRANSPORT).lower().strip()

        return cls(
            headless=env_bool("PLAYWRIGHT_HEADLESS", True),
            browser=env_str("PLAYWRIGHT_BROWSER", "chromium"),
            timeout_ms=env_int("PLAYWRIGHT_TIMEOUT_MS", 30000),
            screenshot_dir=env_str("PLAYWRIGHT_SCREENSHOT_DIR", "/tmp/playwright"),
            mcp_transport=transport,
            mcp_host=env_str("PLAYWRIGHT_MCP_HOST", cls.DEFAULT_MCP_HOST),
            mcp_port=env_int("PLAYWRIGHT_MCP_PORT", cls.DEFAULT_MCP_PORT),
            mcp_url=env_str("PLAYWRIGHT_MCP_URL", cls.DEFAULT_MCP_URL),
        )

    def to_env_overrides(self) -> Dict[str, str]:
        return {
            "PLAYWRIGHT_HEADLESS": str(self.headless).lower(),
            "PLAYWRIGHT_BROWSER": self.browser,
            "PLAYWRIGHT_TIMEOUT_MS": str(self.timeout_ms),
            "PLAYWRIGHT_SCREENSHOT_DIR": self.screenshot_dir,
            "MCP_TRANSPORT": self.mcp_transport,
            "MCP_HOST": self.mcp_host,
            "MCP_PORT": str(self.mcp_port),
        }
