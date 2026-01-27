"""Web Search client using DuckDuckGo."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


class WebSearchClient:
    """Client for web searches using DuckDuckGo."""

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

    def health_check(self) -> Dict[str, Any]:
        """Check DuckDuckGo search availability."""
        try:
            from duckduckgo_search import DDGS
            return {
                "ok": True,
                "service": "DuckDuckGo",
                "max_results": self.max_results,
                "region": self.region,
                "safesearch": self.safesearch,
            }
        except ImportError as e:
            return {
                "ok": False,
                "error": f"duckduckgo-search not installed: {e}",
            }

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
        try:
            from duckduckgo_search import DDGS

            max_res = max_results or self.max_results
            reg = region or self.region

            with DDGS() as ddgs:
                results = list(ddgs.text(
                    query,
                    region=reg,
                    safesearch=self.safesearch,
                    max_results=max_res,
                ))

            formatted = []
            for r in results:
                formatted.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", r.get("link", "")),
                    "snippet": r.get("body", r.get("snippet", ""))[:500],
                })

            return {
                "ok": True,
                "query": query,
                "count": len(formatted),
                "results": formatted,
            }
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
        try:
            from duckduckgo_search import DDGS

            max_res = max_results or self.max_results

            with DDGS() as ddgs:
                results = list(ddgs.news(
                    query,
                    region=self.region,
                    safesearch=self.safesearch,
                    timelimit=timelimit,
                    max_results=max_res,
                ))

            formatted = []
            for r in results:
                formatted.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", r.get("link", "")),
                    "snippet": r.get("body", r.get("excerpt", ""))[:500],
                    "source": r.get("source", ""),
                    "date": r.get("date", ""),
                })

            return {
                "ok": True,
                "query": query,
                "count": len(formatted),
                "timelimit": timelimit,
                "results": formatted,
            }
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
        try:
            from duckduckgo_search import DDGS

            max_res = max_results or self.max_results

            with DDGS() as ddgs:
                results = list(ddgs.images(
                    query,
                    region=self.region,
                    safesearch=self.safesearch,
                    size=size,
                    type_image=image_type,
                    max_results=max_res,
                ))

            formatted = []
            for r in results:
                formatted.append({
                    "title": r.get("title", ""),
                    "image_url": r.get("image", ""),
                    "thumbnail": r.get("thumbnail", ""),
                    "source_url": r.get("url", ""),
                    "width": r.get("width"),
                    "height": r.get("height"),
                })

            return {
                "ok": True,
                "query": query,
                "count": len(formatted),
                "results": formatted,
            }
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
        try:
            from duckduckgo_search import DDGS

            max_res = max_results or self.max_results

            with DDGS() as ddgs:
                results = list(ddgs.videos(
                    query,
                    region=self.region,
                    safesearch=self.safesearch,
                    timelimit=timelimit,
                    resolution=resolution,
                    max_results=max_res,
                ))

            formatted = []
            for r in results:
                formatted.append({
                    "title": r.get("title", ""),
                    "url": r.get("content", r.get("url", "")),
                    "description": r.get("description", "")[:300],
                    "publisher": r.get("publisher", ""),
                    "duration": r.get("duration", ""),
                    "views": r.get("statistics", {}).get("viewCount") if r.get("statistics") else None,
                    "thumbnail": r.get("images", {}).get("large") if r.get("images") else None,
                })

            return {
                "ok": True,
                "query": query,
                "count": len(formatted),
                "results": formatted,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def instant_answer(self, query: str) -> Dict[str, Any]:
        """Get instant answer for a query.

        Args:
            query: Search query

        Returns:
            Instant answer if available
        """
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results = list(ddgs.answers(query))

            if results:
                answer = results[0]
                return {
                    "ok": True,
                    "query": query,
                    "answer": answer.get("text", ""),
                    "url": answer.get("url", ""),
                    "source": answer.get("source", ""),
                }
            return {
                "ok": True,
                "query": query,
                "answer": None,
                "message": "No instant answer available",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def suggestions(self, query: str) -> Dict[str, Any]:
        """Get search suggestions.

        Args:
            query: Partial search query

        Returns:
            Search suggestions
        """
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results = list(ddgs.suggestions(query))

            suggestions = [r.get("phrase", r) for r in results if r]

            return {
                "ok": True,
                "query": query,
                "suggestions": suggestions[:10],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def maps_search(
        self,
        query: str,
        place: Optional[str] = None,
        max_results: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search for places/locations.

        Args:
            query: Search query (e.g., "restaurants", "hotels")
            place: Location to search in (e.g., "New York")
            max_results: Maximum number of results

        Returns:
            Places/locations results
        """
        try:
            from duckduckgo_search import DDGS

            max_res = max_results or self.max_results

            with DDGS() as ddgs:
                results = list(ddgs.maps(
                    query,
                    place=place,
                    max_results=max_res,
                ))

            formatted = []
            for r in results:
                formatted.append({
                    "title": r.get("title", ""),
                    "address": r.get("address", ""),
                    "phone": r.get("phone", ""),
                    "url": r.get("url", ""),
                    "latitude": r.get("latitude"),
                    "longitude": r.get("longitude"),
                    "rating": r.get("rating"),
                    "reviews": r.get("reviews"),
                    "category": r.get("category", ""),
                })

            return {
                "ok": True,
                "query": query,
                "place": place,
                "count": len(formatted),
                "results": formatted,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
