"""Kubernetes MCP utilities.

This package intentionally holds business-logic helpers grouped by area:
- `cluster`: reachability + high-level counts
- `core_resources`: namespaces/nodes/pods/services/events
- `workloads`: deployments + rollout actions
- `access_mgmt`: service accounts
- `terminal`: kubectl-like parsing
"""

__all__ = [
	"access_mgmt",
	"clients",
	"cluster",
	"core_resources",
	"formatting",
	"terminal",
	"workloads",
]
