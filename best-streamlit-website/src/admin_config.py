from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "data" / "admin_config.json"
SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _safe_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        t = v.strip().lower()
        if t in {"1", "true", "yes", "y", "on"}:
            return True
        if t in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _safe_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return str(v)


@dataclass
class AdminConfig:
    """Admin-configurable UI/runtime settings persisted to disk.

    This is intentionally separate from Streamlit's config.toml.

    Design goals:
    - Safe defaults: if the file doesn't exist (or is corrupted), app works.
    - Non-secret by default: credentials/tokens should stay in env vars.
    - Forward-compatible: schema_version guarded.
    """

    schema_version: int = SCHEMA_VERSION
    updated_at: str = field(default_factory=_utc_now_iso)

    # Page visibility toggles: key is the page file path (e.g. "pages/6_Kubernetes.py")
    pages: Dict[str, bool] = field(default_factory=dict)

    # Feature toggles + runtime overrides.
    # Keys are stable ids: jenkins/kubernetes/docker/nexus.
    mcp_servers: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Agent availability + runtime overrides (non-secret).
    # Keys are stable ids: datagen/jenkins_agent/kubernetes_agent.
    agents: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def default(cls, *, known_pages: Optional[list[str]] = None) -> "AdminConfig":
        pages: Dict[str, bool] = {}
        for p in known_pages or []:
            pages[p] = True

        mcp_servers = {
            "jenkins": {"enabled": True, "base_url": None, "verify_ssl": None, "transport": None, "url": None},
            "kubernetes": {"enabled": True, "kubeconfig": None, "context": None, "transport": None, "url": None},
            "docker": {
                "enabled": True,
                "docker_host": None,
                "docker_tls_verify": None,
                "docker_cert_path": None,
                "docker_timeout_seconds": None,
                "transport": None,
                "url": None,
            },
            "nexus": {"enabled": True, "base_url": None, "verify_ssl": None, "allow_raw": None, "transport": None, "url": None},
            "scheduler": {"enabled": True, "transport": None, "url": None},
        }

        agents = {
            "datagen": {"enabled": True, "ollama_base_url": None, "model": None, "temperature": None},
            "jenkins_agent": {"enabled": True},
            "kubernetes_agent": {"enabled": True},
        }

        return cls(pages=pages, mcp_servers=mcp_servers, agents=agents)

    def is_page_enabled(self, page_path: str, *, default: bool = True) -> bool:
        return _safe_bool(self.pages.get(page_path, default), default)

    def is_mcp_enabled(self, server: str, *, default: bool = True) -> bool:
        raw = self.mcp_servers.get(server, {})
        return _safe_bool(raw.get("enabled", default), default)

    def is_agent_enabled(self, agent: str, *, default: bool = True) -> bool:
        raw = self.agents.get(agent, {})
        return _safe_bool(raw.get("enabled", default), default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "updated_at": str(self.updated_at),
            "pages": dict(self.pages),
            "mcp_servers": dict(self.mcp_servers),
            "agents": dict(self.agents),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, raw_json: str, *, known_pages: Optional[list[str]] = None) -> "AdminConfig":
        raw = json.loads(raw_json)
        return _parse_admin_config(raw, known_pages=known_pages)


def _parse_admin_config(raw: Any, *, known_pages: Optional[list[str]] = None) -> AdminConfig:
    default_cfg = AdminConfig.default(known_pages=known_pages)

    if not isinstance(raw, dict):
        return default_cfg

    schema_version = raw.get("schema_version", SCHEMA_VERSION)
    if not isinstance(schema_version, int) or schema_version <= 0:
        schema_version = SCHEMA_VERSION

    pages_raw = raw.get("pages", {})
    pages: Dict[str, bool] = dict(default_cfg.pages)
    if isinstance(pages_raw, dict):
        for k, v in pages_raw.items():
            if not isinstance(k, str):
                continue
            pages[k] = _safe_bool(v, pages.get(k, True))

    mcp_raw = raw.get("mcp_servers", {})
    mcp_servers: Dict[str, Dict[str, Any]] = dict(default_cfg.mcp_servers)
    if isinstance(mcp_raw, dict):
        for server, settings in mcp_raw.items():
            if not isinstance(server, str) or not isinstance(settings, dict):
                continue
            merged = dict(mcp_servers.get(server, {}))
            for k, v in settings.items():
                merged[str(k)] = v
            mcp_servers[server] = merged

    agents_raw = raw.get("agents", {})
    agents: Dict[str, Dict[str, Any]] = dict(default_cfg.agents)
    if isinstance(agents_raw, dict):
        for agent, settings in agents_raw.items():
            if not isinstance(agent, str) or not isinstance(settings, dict):
                continue
            merged = dict(agents.get(agent, {}))
            for k, v in settings.items():
                merged[str(k)] = v
            agents[agent] = merged

    updated_at = _safe_str(raw.get("updated_at")) or default_cfg.updated_at

    return AdminConfig(
        schema_version=schema_version,
        updated_at=updated_at,
        pages=pages,
        mcp_servers=mcp_servers,
        agents=agents,
    )


def load_admin_config(
    *,
    path: Path = DEFAULT_CONFIG_PATH,
    known_pages: Optional[list[str]] = None,
) -> AdminConfig:
    """Load admin config from disk.

    If missing/corrupt, returns defaults.
    If new pages exist, they are auto-added as enabled.
    """

    default_cfg = AdminConfig.default(known_pages=known_pages)

    if not path.exists():
        return default_cfg

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_cfg

    return _parse_admin_config(raw, known_pages=known_pages)


def save_admin_config(cfg: AdminConfig, *, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": int(cfg.schema_version),
        "updated_at": _utc_now_iso(),
        "pages": dict(cfg.pages),
        "mcp_servers": dict(cfg.mcp_servers),
        "agents": dict(cfg.agents),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
