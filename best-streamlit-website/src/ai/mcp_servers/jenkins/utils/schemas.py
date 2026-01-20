from __future__ import annotations

import inspect
from typing import Any, Dict, List

from .client import JenkinsMCPServer


def _schema_from_signature(sig: inspect.Signature) -> Dict[str, Any]:
    """Build a simple JSON-style input schema from a function signature."""

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for name, param in sig.parameters.items():
        if name == "self":
            continue

        param_type = "string"
        lname = name.lower()
        if "number" in lname or lname.endswith("_id") or lname.endswith("id") or "count" in lname:
            param_type = "integer"
        elif lname.startswith("is_") or lname.startswith("has_") or "enable" in lname or "disable" in lname:
            param_type = "boolean"

        properties[name] = {
            "type": param_type,
            "description": f"Argument '{name}' for tool call.",
        }
        if param.default is inspect._empty:
            required.append(name)

    return {"type": "object", "properties": properties, "required": required}


def describe_jenkins_tools() -> Dict[str, Dict[str, Any]]:
    """Describe JenkinsMCPServer methods as MCP-style tools."""

    tool_defs: Dict[str, Dict[str, Any]] = {}

    for name, fn in inspect.getmembers(JenkinsMCPServer, predicate=callable):
        if name.startswith("_") or name == "__init__":
            continue

        doc = (inspect.getdoc(fn) or "Jenkins tool.").strip().splitlines()[0]
        try:
            sig = inspect.signature(fn)
        except Exception:  # noqa: BLE001
            input_schema = {"type": "object", "properties": {}, "required": []}
        else:
            input_schema = _schema_from_signature(sig)

        tool_defs[name] = {
            "name": name,
            "description": doc,
            "input_schema": input_schema,
        }

    return tool_defs
