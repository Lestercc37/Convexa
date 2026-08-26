from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the backend application."""

    model_config = SettingsConfigDict(env_prefix="QLL_", env_file=".env", extra="ignore")

    app_name: str = Field(default="QLL Eagle Platform")
    version: str = Field(default="0.1.0")
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    openapi_url: str = Field(default="/openapi.json")
    docs_url: str = Field(default="/docs")
    redoc_url: str = Field(default="/redoc")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./qll_eagle.db",
        validation_alias=AliasChoices("DATABASE_URL", "QLL_DATABASE_URL"),
    )
    database_echo: bool = Field(default=False)
    enable_scheduler: bool = Field(default=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
