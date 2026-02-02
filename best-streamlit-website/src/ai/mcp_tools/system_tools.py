"""System utilities for Agent Lab.

These tools are auto-discovered by FileSystemProvider and provide
system-level utilities for agents.

Usage with FileSystemProvider:
    from fastmcp.server.providers.filesystem import FileSystemProvider
    provider = FileSystemProvider(Path(__file__).parent, reload=True)
"""

from __future__ import annotations

import platform
from datetime import datetime
from typing import Any, Dict, List

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False

from fastmcp.tools import tool


@tool
def get_system_info() -> Dict[str, Any]:
    """Get basic system information.

    Returns:
        Dict with system details (OS, architecture, Python version, etc.)
    """
    return {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "hostname": platform.node(),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@tool
def get_resource_usage() -> Dict[str, Any]:
    """Get current system resource usage.

    Returns:
        Dict with CPU, memory, and disk usage metrics
    """
    if not PSUTIL_AVAILABLE:
        return {
            "error": "psutil not installed",
            "message": "Install psutil for resource usage metrics",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    return {
        "cpu": {
            "percent": cpu_percent,
            "count_logical": psutil.cpu_count(),
            "count_physical": psutil.cpu_count(logical=False),
        },
        "memory": {
            "total_gb": round(memory.total / (1024**3), 2),
            "available_gb": round(memory.available / (1024**3), 2),
            "used_gb": round(memory.used / (1024**3), 2),
            "percent": memory.percent,
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
            "percent": round(disk.percent, 1),
        },
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@tool
def get_process_list(limit: int = 20) -> List[Dict[str, Any]]:
    """Get list of running processes sorted by CPU usage.

    Args:
        limit: Maximum number of processes to return (default 20)

    Returns:
        List of process info dicts with name, pid, CPU%, memory%
    """
    if not PSUTIL_AVAILABLE:
        return [{"error": "psutil not installed"}]

    processes = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = proc.info
            processes.append({
                "pid": info["pid"],
                "name": info["name"],
                "cpu_percent": round(info["cpu_percent"] or 0, 1),
                "memory_percent": round(info["memory_percent"] or 0, 1),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Sort by CPU usage descending
    processes.sort(key=lambda x: x["cpu_percent"], reverse=True)
    return processes[:limit]


@tool
def get_network_info() -> Dict[str, Any]:
    """Get network interface information.

    Returns:
        Dict with network interface details and statistics
    """
    if not PSUTIL_AVAILABLE:
        return {
            "error": "psutil not installed",
            "message": "Install psutil for network info",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    interfaces = {}
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    for name, addr_list in addrs.items():
        interface_info = {
            "addresses": [],
            "is_up": False,
            "speed_mbps": None,
        }

        for addr in addr_list:
            interface_info["addresses"].append({
                "family": str(addr.family.name) if hasattr(addr.family, "name") else str(addr.family),
                "address": addr.address,
                "netmask": addr.netmask,
            })

        if name in stats:
            s = stats[name]
            interface_info["is_up"] = s.isup
            interface_info["speed_mbps"] = s.speed if s.speed > 0 else None

        interfaces[name] = interface_info

    # Get IO counters
    io = psutil.net_io_counters()

    return {
        "interfaces": interfaces,
        "io_counters": {
            "bytes_sent": io.bytes_sent,
            "bytes_recv": io.bytes_recv,
            "packets_sent": io.packets_sent,
            "packets_recv": io.packets_recv,
        },
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
