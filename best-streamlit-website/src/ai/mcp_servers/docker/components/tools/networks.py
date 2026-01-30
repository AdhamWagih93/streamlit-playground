"""Docker network and volume management tool implementations."""
from __future__ import annotations

from typing import Any, Dict, List

from ...client import client_or_error


def list_networks() -> Dict[str, Any]:
    """List Docker networks."""
    cli, err = client_or_error()
    if err:
        return err
    nets = cli.networks.list()
    rows: List[Dict[str, Any]] = []
    for n in nets:
        attrs = getattr(n, "attrs", {}) or {}
        rows.append({"id": getattr(n, "id", None), "name": getattr(n, "name", None), "driver": attrs.get("Driver")})
    return {"ok": True, "networks": rows}


def list_volumes() -> Dict[str, Any]:
    """List Docker volumes."""
    cli, err = client_or_error()
    if err:
        return err
    vols = cli.volumes.list()
    rows: List[Dict[str, Any]] = []
    for v in vols or []:
        attrs = getattr(v, "attrs", {}) or {}
        rows.append({"name": attrs.get("Name"), "driver": attrs.get("Driver"), "mountpoint": attrs.get("Mountpoint")})
    return {"ok": True, "volumes": rows}
