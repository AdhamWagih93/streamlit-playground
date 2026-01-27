from __future__ import annotations

import inspect
import os
from typing import Any, Dict, Optional

from fastmcp import FastMCP

from .config import WebSearchMCPServerConfig
from .utils.client import WebSearchClient


mcp = FastMCP("websearch-mcp")

_CLIENT: Optional[WebSearchClient] = None


def _client_from_env() -> WebSearchClient:
    """Get or create a WebSearchClient from environment configuration."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg = WebSearchMCPServerConfig.from_env()
    _CLIENT = WebSearchClient(
        max_results=cfg.max_results,
        region=cfg.region,
        safesearch=cfg.safesearch,
        timeout_seconds=cfg.timeout_seconds,
    )
    return _CLIENT


@mcp.tool
def websearch_health_check() -> Dict[str, Any]:
    """Check web search availability and configuration."""
    c = _client_from_env()
    return c.health_check()


@mcp.tool
def websearch_search(
    query: str,
    max_results: Optional[int] = None,
    region: Optional[str] = None,
) -> Dict[str, Any]:
    """Perform a web search using DuckDuckGo.

    Args:
        query: Search query
        max_results: Maximum number of results (default: 10)
        region: Region code (e.g., us-en, uk-en, wt-wt for global)
    """
    c = _client_from_env()
    return c.search(query, max_results, region)


@mcp.tool
def websearch_news(
    query: str,
    max_results: Optional[int] = None,
    timelimit: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for news articles.

    Args:
        query: Search query
        max_results: Maximum number of results
        timelimit: Time limit - d=day, w=week, m=month
    """
    c = _client_from_env()
    return c.news_search(query, max_results, timelimit)


@mcp.tool
def websearch_images(
    query: str,
    max_results: Optional[int] = None,
    size: Optional[str] = None,
    image_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for images.

    Args:
        query: Search query
        max_results: Maximum number of results
        size: Image size (Small, Medium, Large, Wallpaper)
        image_type: Image type (photo, clipart, gif, transparent, line)
    """
    c = _client_from_env()
    return c.image_search(query, max_results, size, image_type)


@mcp.tool
def websearch_videos(
    query: str,
    max_results: Optional[int] = None,
    timelimit: Optional[str] = None,
    resolution: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for videos.

    Args:
        query: Search query
        max_results: Maximum number of results
        timelimit: Time limit - d=day, w=week, m=month
        resolution: Video resolution (high, standard)
    """
    c = _client_from_env()
    return c.video_search(query, max_results, timelimit, resolution)


@mcp.tool
def websearch_instant_answer(query: str) -> Dict[str, Any]:
    """Get instant answer for a query (like calculator, definitions, etc.).

    Args:
        query: Query to get instant answer for
    """
    c = _client_from_env()
    return c.instant_answer(query)


@mcp.tool
def websearch_suggestions(query: str) -> Dict[str, Any]:
    """Get search suggestions for a partial query.

    Args:
        query: Partial search query
    """
    c = _client_from_env()
    return c.suggestions(query)


@mcp.tool
def websearch_maps(
    query: str,
    place: Optional[str] = None,
    max_results: Optional[int] = None,
) -> Dict[str, Any]:
    """Search for places and locations.

    Args:
        query: Search query (e.g., "restaurants", "hotels", "coffee shops")
        place: Location to search in (e.g., "New York", "London")
        max_results: Maximum number of results
    """
    c = _client_from_env()
    return c.maps_search(query, place, max_results)


def run_stdio() -> None:
    """Run the Web Search MCP server over HTTP."""
    cfg = WebSearchMCPServerConfig.from_env()

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
