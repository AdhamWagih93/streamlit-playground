"""Fake VaultClient for local/CI testing.

Reads secrets from a JSON file (default: ``localdev/secrets.local.json``,
overridable via ``LOCALDEV_SECRETS``) instead of the real platform vault.
Mirrors the only method the dashboard calls:
``read_all_nested_secrets(path[, sub])``.

The JSON is shaped ``{ "<vault_path>": { ...nested... } }`` so a read of path
``new_git`` returns the nested ADO block, ``postgres`` the PG creds, etc. Any
path absent from the file returns ``{}`` — which the dashboard treats as "that
integration is not configured" and degrades gracefully.
"""

from __future__ import annotations

import json
import os

_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "secrets.local.json")


def _load() -> dict:
    path = os.environ.get("LOCALDEV_SECRETS", _DEFAULT)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class VaultClient:
    def __init__(self, *args, **kwargs):
        # The real client re-initialises an auth token here; nothing to do.
        self._data = _load()

    def read_all_nested_secrets(self, path: str, sub: str = "") -> dict:
        node = dict(self._data.get(path) or {})
        # Postgres creds are env-overridable so CI (or a local `docker run
        # postgres`) can point the dashboard at a real DB without editing the
        # committed secrets file. Unset env → keeps the JSON value (host "" =
        # unconfigured = graceful degradation).
        if path == "postgres":
            for key, env in (("host", "LOCALDEV_PG_HOST"),
                             ("port", "LOCALDEV_PG_PORT"),
                             ("database", "LOCALDEV_PG_DB"),
                             ("username", "LOCALDEV_PG_USER"),
                             ("password", "LOCALDEV_PG_PASSWORD")):
                val = os.environ.get(env)
                if val:
                    node[key] = val
        if sub:
            node = (node or {}).get(sub) or {}
        return dict(node) if isinstance(node, dict) else {}
