from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Dict

from langchain_mcp_adapters.client import MultiServerMCPClient


def _env_subset(prefixes: tuple[str, ...]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in os.environ.items():
        if k.startswith(prefixes):
            out[k] = v
    return dict(sorted(out.items()))


async def _list_tools(connections: Dict[str, Any]) -> None:
    client = MultiServerMCPClient(connections=connections)
    tools = await client.get_tools()
    names = sorted({str(getattr(t, "name", "")) for t in (tools or []) if getattr(t, "name", None)})
    print(f"Loaded {len(names)} tools")
    for n in names:
        print("-", n)


def main() -> int:
    p = argparse.ArgumentParser(description="Inspect MCP tools exposed via stdio or SSE")
    p.add_argument("--server", choices=["helm", "docker", "all"], default="all")
    p.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    p.add_argument("--url", default="", help="SSE URL (used when --transport=sse)")
    args = p.parse_args()

    print("Python:", sys.executable)
    print("Relevant env (STREAMLIT_*, DOCKER_*, HELM_*, K8S_*, *_MCP_*):")
    print(_env_subset(("STREAMLIT_", "DOCKER_", "HELM_", "K8S_", "JENKINS_")))

    connections: Dict[str, Any] = {}

    def add_stdio(name: str, module: str) -> None:
        connections[name] = {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", module],
            "env": dict(os.environ),
        }

    def add_sse(name: str, url: str) -> None:
        if not url:
            raise SystemExit("--url is required when --transport=sse")
        connections[name] = {"transport": "sse", "url": url}

    if args.transport == "stdio":
        # Helm tools now live under kubernetes-mcp; there is no standalone
        # helm-mcp process. Keep docker-mcp for local inspection.
        if args.server in {"docker", "all"}:
            add_stdio("docker", "src.ai.mcp_servers.docker.mcp")
    else:
        # Single URL mode: connect one server at a time
        if args.server == "all":
            raise SystemExit("--server must be docker when using --transport=sse")
        add_sse(args.server, args.url)

    asyncio.run(_list_tools(connections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
