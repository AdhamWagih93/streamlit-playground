from __future__ import annotations

import inspect
import os
from typing import Any, Dict, Optional

from fastmcp import FastMCP

from .config import PlaywrightMCPServerConfig
from .utils.client import PlaywrightClient


mcp = FastMCP("playwright-mcp")

_CLIENT: Optional[PlaywrightClient] = None


def _client_from_env() -> PlaywrightClient:
    """Get or create a PlaywrightClient from environment configuration."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg = PlaywrightMCPServerConfig.from_env()
    _CLIENT = PlaywrightClient(
        headless=cfg.headless,
        browser_type=cfg.browser,
        timeout_ms=cfg.timeout_ms,
        screenshot_dir=cfg.screenshot_dir,
    )
    return _CLIENT


@mcp.tool
def playwright_health_check() -> Dict[str, Any]:
    """Check Playwright availability and configuration."""
    c = _client_from_env()
    return c.health_check()


@mcp.tool
def playwright_navigate(
    url: str,
    wait_until: str = "domcontentloaded",
) -> Dict[str, Any]:
    """Navigate to a URL in the browser.

    Args:
        url: URL to navigate to (https:// prefix added if missing)
        wait_until: When to consider navigation complete (load, domcontentloaded, networkidle)
    """
    c = _client_from_env()
    return c.navigate(url, wait_until)


@mcp.tool
def playwright_get_content(
    selector: Optional[str] = None,
) -> Dict[str, Any]:
    """Get page content or specific element content.

    Args:
        selector: Optional CSS selector to get specific element (gets full page if not provided)
    """
    c = _client_from_env()
    return c.get_page_content(selector)


@mcp.tool
def playwright_screenshot(
    full_page: bool = False,
    selector: Optional[str] = None,
) -> Dict[str, Any]:
    """Take a screenshot of the current page or element.

    Args:
        full_page: Capture full scrollable page
        selector: Capture specific element only
    """
    c = _client_from_env()
    return c.screenshot(full_page, selector)


@mcp.tool
def playwright_click(selector: str) -> Dict[str, Any]:
    """Click an element on the page.

    Args:
        selector: CSS selector for the element to click
    """
    c = _client_from_env()
    return c.click(selector)


@mcp.tool
def playwright_fill(selector: str, value: str) -> Dict[str, Any]:
    """Fill an input field with a value.

    Args:
        selector: CSS selector for the input field
        value: Value to fill in
    """
    c = _client_from_env()
    return c.fill(selector, value)


@mcp.tool
def playwright_type(
    selector: str,
    text: str,
    delay: int = 50,
) -> Dict[str, Any]:
    """Type text character by character (simulates real typing).

    Args:
        selector: CSS selector for the input
        text: Text to type
        delay: Delay between keystrokes in milliseconds
    """
    c = _client_from_env()
    return c.type_text(selector, text, delay)


@mcp.tool
def playwright_press_key(
    key: str,
    selector: Optional[str] = None,
) -> Dict[str, Any]:
    """Press a keyboard key.

    Args:
        key: Key to press (e.g., Enter, Tab, Escape, ArrowDown)
        selector: Optional element to focus first
    """
    c = _client_from_env()
    return c.press_key(key, selector)


@mcp.tool
def playwright_wait_for(
    selector: str,
    state: str = "visible",
) -> Dict[str, Any]:
    """Wait for an element to reach a certain state.

    Args:
        selector: CSS selector for the element
        state: State to wait for (attached, detached, visible, hidden)
    """
    c = _client_from_env()
    return c.wait_for_selector(selector, state)


@mcp.tool
def playwright_get_links(selector: str = "a") -> Dict[str, Any]:
    """Get all links on the current page.

    Args:
        selector: CSS selector for link elements (default: all anchor tags)
    """
    c = _client_from_env()
    return c.get_links(selector)


@mcp.tool
def playwright_get_forms() -> Dict[str, Any]:
    """Get all forms on the current page with their inputs."""
    c = _client_from_env()
    return c.get_forms()


@mcp.tool
def playwright_evaluate(script: str) -> Dict[str, Any]:
    """Execute JavaScript code on the page.

    Args:
        script: JavaScript code to execute
    """
    c = _client_from_env()
    return c.evaluate(script)


@mcp.tool
def playwright_page_info() -> Dict[str, Any]:
    """Get information about the current page (URL, title, viewport)."""
    c = _client_from_env()
    return c.get_page_info()


@mcp.tool
def playwright_scroll(
    direction: str = "down",
    amount: int = 500,
) -> Dict[str, Any]:
    """Scroll the page.

    Args:
        direction: Scroll direction (up, down)
        amount: Pixels to scroll
    """
    c = _client_from_env()
    return c.scroll(direction, amount)


@mcp.tool
def playwright_back() -> Dict[str, Any]:
    """Go back in browser history."""
    c = _client_from_env()
    return c.go_back()


@mcp.tool
def playwright_forward() -> Dict[str, Any]:
    """Go forward in browser history."""
    c = _client_from_env()
    return c.go_forward()


@mcp.tool
def playwright_close() -> Dict[str, Any]:
    """Close the browser instance."""
    c = _client_from_env()
    return c.close()


def run_stdio() -> None:
    """Run the Playwright MCP server over HTTP."""
    cfg = PlaywrightMCPServerConfig.from_env()

    host = os.environ.get("MCP_HOST") or cfg.mcp_host
    port_raw = os.environ.get("MCP_PORT")
    try:
        port = int(port_raw) if port_raw else int(cfg.mcp_port)
    except Exception:
        port = int(cfg.mcp_port)

    try:
        mcp.run(transport="http", host=host, port=port)
    except TypeError:
        mcp.run(transport="http")


if __name__ == "__main__":
    run_stdio()
