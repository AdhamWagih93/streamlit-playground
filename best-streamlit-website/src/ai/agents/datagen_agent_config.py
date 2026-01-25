from __future__ import annotations

from dataclasses import dataclass

from src.config_utils import env_str
from src.admin_config import load_admin_config


@dataclass(frozen=True)
class DataGenAgentConfig:
    """Config for the DataGen agent (local tools, no MCP)."""

    ollama_base_url: str
    model: str
    temperature: float
    enabled: bool  # Whether Ollama is enabled

    DEFAULT_BASE_URL: str = "http://localhost:11434"
    DEFAULT_MODEL: str = "tinyllama"  # Changed to lightweight model
    DEFAULT_TEMPERATURE: float = 0.0
    DEFAULT_ENABLED: bool = True

    @classmethod
    def from_env(cls) -> "DataGenAgentConfig":
        base_url = env_str("DATAGEN_OLLAMA_BASE_URL", env_str("OLLAMA_BASE_URL", cls.DEFAULT_BASE_URL))
        model = env_str("DATAGEN_OLLAMA_MODEL", env_str("OLLAMA_MODEL", cls.DEFAULT_MODEL))

        raw_temp = env_str("DATAGEN_OLLAMA_TEMPERATURE", env_str("OLLAMA_TEMPERATURE", str(cls.DEFAULT_TEMPERATURE)))
        try:
            temp = float(raw_temp)
        except Exception:
            temp = cls.DEFAULT_TEMPERATURE

        # Check if Ollama is enabled
        raw_enabled = env_str("OLLAMA_ENABLED", str(cls.DEFAULT_ENABLED)).lower()
        enabled = raw_enabled in ("true", "1", "yes", "on")

        return cls(ollama_base_url=base_url, model=model, temperature=temp, enabled=enabled)

    @classmethod
    def load(cls) -> "DataGenAgentConfig":
        """Load config from env and apply admin overrides (if present)."""

        cfg = cls.from_env()
        admin = load_admin_config()
        raw = (admin.agents or {}).get("datagen", {})
        if not isinstance(raw, dict):
            return cfg

        # Back-compat: earlier drafts used "base_url".
        base_url = raw.get("ollama_base_url") or raw.get("base_url")
        model = raw.get("model")
        temperature = raw.get("temperature")
        enabled = raw.get("enabled")

        effective_base_url = str(base_url).strip() if isinstance(base_url, str) and base_url.strip() else cfg.ollama_base_url
        effective_model = str(model).strip() if isinstance(model, str) and model.strip() else cfg.model

        effective_temp = cfg.temperature
        if temperature is not None:
            try:
                effective_temp = float(temperature)
            except Exception:
                effective_temp = cfg.temperature

        effective_enabled = cfg.enabled
        if enabled is not None:
            effective_enabled = bool(enabled)

        return cls(ollama_base_url=effective_base_url, model=effective_model, temperature=effective_temp, enabled=effective_enabled)
