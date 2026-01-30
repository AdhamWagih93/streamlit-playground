"""Web Search client using Tavily."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


class WebSearchClient:
    """Client for web searches using Tavily."""

    def __init__(
        self,
        max_results: int = 10,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timeout_seconds: int = 30,
    ):
        self.max_results = max_results
        self.region = region
        self.safesearch = safesearch
        self.timeout_seconds = timeout_seconds

    def _api_key(self) -> str:
        return (os.environ.get("TAVILY_API_KEY") or "").strip()

    def _tavily(self):
        from tavily import TavilyClient

        api_key = self._api_key()
        if not api_key:
            raise RuntimeError(
                "TAVILY_API_KEY is not set. Add it to your .env and restart the websearch-mcp service."
            )

        # The official client doesn't expose a timeout parameter; if needed, we'd switch to raw requests.
        return TavilyClient(api_key=api_key)

    def _search(
        self,
        query: str,
        max_results: Optional[int] = None,
        *,
        topic: str = "general",
        include_answer: bool = False,
        include_images: bool = False,
        include_raw_content: bool = False,
        search_depth: str = "basic",
    ) -> Dict[str, Any]:
        client = self._tavily()

        max_res = int(max_results or self.max_results)
        payload = client.search(
            query=query,
            max_results=max_res,
            topic=topic,
            search_depth=search_depth,
            include_answer=include_answer,
            include_images=include_images,
            include_raw_content=include_raw_content,
        )

        # Normalize into our existing structure.
        results_in = payload.get("results") or []
        formatted: List[Dict[str, Any]] = []
        for r in results_in:
            if not isinstance(r, dict):
                continue
            formatted.append(
                {
                    "title": r.get("title") or "",
                    "url": r.get("url") or "",
                    "snippet": (r.get("content") or "")[:500],
                    "score": r.get("score"),
                }
            )

        out: Dict[str, Any] = {
            "ok": True,
            "provider": "tavily",
            "query": query,
            "count": len(formatted),
            "results": formatted,
        }
        if "answer" in payload:
            out["answer"] = payload.get("answer")
        if include_images and "images" in payload:
            out["images"] = payload.get("images")
        return out

    def health_check(self) -> Dict[str, Any]:
        try:
            # Import check
            from tavily import TavilyClient  # noqa: F401

            has_key = bool(self._api_key())
            return {
                "ok": has_key,
                "service": "Tavily",
                "has_api_key": has_key,
                "max_results": self.max_results,
                "region": self.region,
                "safesearch": self.safesearch,
                **({"message": "TAVILY_API_KEY is not set"} if not has_key else {}),
            }
        except ImportError as e:
            return {
                "ok": False,
                "error": f"tavily-python not installed: {e}",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Perform a web search.

        Args:
            query: Search query
            max_results: Maximum number of results (default: config value)
            region: Region code (default: config value)

        Returns:
            Search results with title, URL, and snippet
        """
        # Tavily does not use region; keep the parameter for backward compatibility.
        _ = region
        try:
            return self._search(query, max_results=max_results, topic="general")
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def news_search(
        self,
        query: str,
        max_results: Optional[int] = None,
        timelimit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search for news articles.

        Args:
            query: Search query
            max_results: Maximum number of results
            timelimit: Time limit (d=day, w=week, m=month)

        Returns:
            News results
        """
        # Tavily doesn't support DuckDuckGo-style timelimit; keep the parameter but ignore.
        _ = timelimit
        try:
            return self._search(query, max_results=max_results, topic="news")
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def image_search(
        self,
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

        Returns:
            Image search results
        """
        # Tavily doesn't support size/type filters like DuckDuckGo; keep parameters but ignore.
        _ = size
        _ = image_type
        try:
            return self._search(query, max_results=max_results, include_images=True)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def video_search(
        self,
        query: str,
        max_results: Optional[int] = None,
        timelimit: Optional[str] = None,
        resolution: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search for videos.

        Args:
            query: Search query
            max_results: Maximum number of results
            timelimit: Time limit (d=day, w=week, m=month)
            resolution: Video resolution (high, standard)

        Returns:
            Video search results
        """
        # Tavily doesn't provide a dedicated video search endpoint.
        _ = timelimit
        _ = resolution
        return {
            "ok": False,
            "provider": "tavily",
            "error": "Video search is not supported by the current Tavily integration.",
        }

    def instant_answer(self, query: str) -> Dict[str, Any]:
        """Return a direct answer (when available) using Tavily's answer field."""
        try:
            return self._search(query, max_results=3, include_answer=True)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def suggestions(self, query: str) -> Dict[str, Any]:
        """Return suggestions for a query.

        Tavily does not provide suggestions/autocomplete; this is kept for API compatibility.
        """
        return {
            "ok": False,
            "provider": "tavily",
            "error": "Suggestions are not supported by the current Tavily integration.",
            "query": query,
        }

    def maps_search(self, query: str, place: Optional[str] = None, max_results: Optional[int] = None) -> Dict[str, Any]:
        """Return map/place results.

        Tavily does not provide maps; this is kept for API compatibility.
        """
        _ = max_results
        q = query if not place else f"{query} near {place}"
        return {
            "ok": False,
            "provider": "tavily",
            "error": "Maps search is not supported by the current Tavily integration.",
            "query": q,
        }
