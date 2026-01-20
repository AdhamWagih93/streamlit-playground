from __future__ import annotations

from dataclasses import dataclass

from src.config_utils import env_str


@dataclass(frozen=True)
class DataGenAgentConfig:
    """Config for the DataGen agent (local tools, no MCP)."""

    ollama_base_url: str
    model: str
    temperature: float

    DEFAULT_BASE_URL: str = "http://localhost:11434"
    DEFAULT_MODEL: str = "qwen2.5:7b-instruct-q6_K"
    DEFAULT_TEMPERATURE: float = 0.0

    @classmethod
    def from_env(cls) -> "DataGenAgentConfig":
        base_url = env_str("DATAGEN_OLLAMA_BASE_URL", env_str("OLLAMA_BASE_URL", cls.DEFAULT_BASE_URL))
        model = env_str("DATAGEN_OLLAMA_MODEL", env_str("OLLAMA_MODEL", cls.DEFAULT_MODEL))

        raw_temp = env_str("DATAGEN_OLLAMA_TEMPERATURE", env_str("OLLAMA_TEMPERATURE", str(cls.DEFAULT_TEMPERATURE)))
        try:
            temp = float(raw_temp)
        except Exception:
            temp = cls.DEFAULT_TEMPERATURE

        return cls(ollama_base_url=base_url, model=model, temperature=temp)
