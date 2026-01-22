"""MCP server implementations used by local AI agents.

Currently includes Jenkins, Kubernetes, and Docker MCP servers. Helm
functionality is exposed via the kubernetes-mcp server (there is no
standalone ``helm-mcp`` process anymore). Shared Helm utilities now live
under :mod:`src.ai.mcp_servers.kubernetes.utils.helm`.
"""