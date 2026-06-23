"""Application configuration.

All settings are read from environment variables (12-factor style). Sensible
defaults are provided for local development; in production every value below
should be supplied through the deployment environment or a .env file.

PostgreSQL connection settings are intentionally surfaced as discrete
placeholders so they can be wired into any secret store. See .env.example.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Application -------------------------------------------------------
    app_name: str = "Trackly"
    app_env: str = Field(default="development")  # development | production | test
    debug: bool = Field(default=True)
    api_prefix: str = "/api"

    # --- PostgreSQL (placeholders — provide via environment) --------------
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="trackly")
    postgres_user: str = Field(default="trackly")
    postgres_password: str = Field(default="trackly")
    # If DATABASE_URL is set it takes precedence over the discrete fields above.
    database_url: str | None = Field(default=None)

    # --- Security ----------------------------------------------------------
    secret_key: str = Field(default="change-me-in-production-please-32+chars")
    access_token_expire_minutes: int = Field(default=60 * 24)  # 24h
    refresh_token_expire_minutes: int = Field(default=60 * 24 * 14)  # 14d
    algorithm: str = "HS256"

    # --- CORS --------------------------------------------------------------
    # NoDecode stops pydantic-settings from JSON-parsing the env value so our
    # validator below can accept a plain comma-separated string.
    cors_origins: Annotated[List[str], NoDecode] = Field(
        default=["http://localhost:5173", "http://localhost:3000"]
    )

    # --- File storage ------------------------------------------------------
    attachments_dir: str = Field(default="/data/attachments")
    max_attachment_mb: int = Field(default=25)

    # --- First-run bootstrap admin ----------------------------------------
    bootstrap_admin_email: str = Field(default="admin@trackly.local")
    bootstrap_admin_password: str = Field(default="admin")
    bootstrap_admin_username: str = Field(default="admin")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def sqlalchemy_database_uri(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
