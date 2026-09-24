from __future__ import annotations

from functools import lru_cache
from typing import Literal

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
    # "mock" (default, no external dependency) or "thetadata" (real Theta
    # Terminal v3, local REST + WebSocket) — switchable without a code
    # change specifically so a real-data incident can be diagnosed by
    # falling back to Mock without spending real ThetaData quota.
    data_provider: Literal["mock", "thetadata"] = Field(default="mock")
    thetadata_rest_url: str = Field(default="http://localhost:25503")
    thetadata_ws_url: str = Field(default="ws://127.0.0.1:25520/v1/events")
    # Local-only TCP link (see backend/core/whale_alerts_relay.py): worker.py
    # forwards every trade/quote event to backend/whale_alerts_worker.py's
    # own process over this, so whale-alerts' own CPU-bound classification
    # never again shares a GIL with the process that owns the real ThetaData
    # WebSocket connection. Never reaches the network beyond this machine.
    whale_alerts_relay_host: str = Field(default="127.0.0.1")
    whale_alerts_relay_port: int = Field(default=25599)


@lru_cache
def get_settings() -> Settings:
    return Settings()
