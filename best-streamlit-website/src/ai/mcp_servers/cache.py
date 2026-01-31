from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlparse


def _bool_env(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_redis_url(url: str) -> dict:
    parsed = urlparse(url)
    host = parsed.hostname or "redis"
    port = int(parsed.port or 6379)
    password = parsed.password
    db = 0
    if parsed.path and parsed.path != "/":
        try:
            db = int(parsed.path.lstrip("/"))
        except Exception:
            db = 0
    return {"host": host, "port": port, "password": password, "db": db}


def configure_mcp_cache(mcp, *, server_name: str, prefix: Optional[str] = None) -> None:
    """Attach Redis-backed response caching middleware to a FastMCP server.

    Enabled via env:
      - MCP_CACHE_ENABLED=true
      - MCP_CACHE_BACKEND=redis
      - MCP_REDIS_URL=redis://[:password]@host:port/db (optional)
      - MCP_REDIS_HOST / MCP_REDIS_PORT / MCP_REDIS_PASSWORD / MCP_REDIS_DB (optional)
    """
    if getattr(mcp, "_cache_configured", False):
        return

    enabled = _bool_env(os.environ.get("MCP_CACHE_ENABLED"))
    backend = (os.environ.get("MCP_CACHE_BACKEND") or "").strip().lower()
    if not enabled or backend != "redis":
        return

    try:
        from fastmcp.server.middleware.caching import ResponseCachingMiddleware
        from key_value.aio.stores.redis import RedisStore
        from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper
    except Exception:
        return

    redis_url = os.environ.get("MCP_REDIS_URL")
    if redis_url:
        cfg = _parse_redis_url(redis_url)
        host = cfg["host"]
        port = cfg["port"]
        password = cfg["password"]
        db = cfg["db"]
    else:
        host = os.environ.get("MCP_REDIS_HOST", "redis")
        port = int(os.environ.get("MCP_REDIS_PORT", "6379"))
        password = os.environ.get("MCP_REDIS_PASSWORD") or None
        db = int(os.environ.get("MCP_REDIS_DB", "0"))

    # Fail open: if Redis isn't reachable (common when running a single service
    # from compose), skip caching rather than crashing all requests.
    try:
        import redis  # type: ignore

        client = redis.Redis(
            host=host,
            port=port,
            password=password,
            db=db,
            socket_connect_timeout=float(os.environ.get("MCP_REDIS_CONNECT_TIMEOUT", "0.25")),
            socket_timeout=float(os.environ.get("MCP_REDIS_TIMEOUT", "0.25")),
        )
        client.ping()
    except Exception:
        return

    base_store = RedisStore(host=host, port=port, password=password, db=db)
    namespaced = PrefixCollectionsWrapper(
        key_value=base_store,
        prefix=prefix or server_name,
    )

    mcp.add_middleware(ResponseCachingMiddleware(cache_storage=namespaced))
    mcp._cache_configured = True
