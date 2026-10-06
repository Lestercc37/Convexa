from __future__ import annotations

import secrets
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
    # Whether the REST scheduler also feeds WhaleAlertsEngine.process() (BVC:
    # volume deltas between chain snapshots) in addition to the live trade
    # stream's Lee-Ready alerts. None = decide by provider (see
    # whale_alerts_bvc_active): off with ThetaData, where the stream already
    # classifies every trade and the BVC alerts only duplicated it (and
    # contradicted its direction, and inherited volume-counter artifacts);
    # on otherwise (Mock has no trade stream, so BVC is its only source).
    # Set QLL_WHALE_ALERTS_BVC_ENABLED=true/false to force either way, no
    # code change.
    whale_alerts_bvc_enabled: bool | None = Field(default=None)
    # Local-only TCP link (see backend/core/stream_processor_relay.py):
    # worker.py forwards raw QUOTE/TRADE WS frames to
    # backend/stream_processor_worker.py's own process over this, and
    # gets the classified result back over the same connection, so
    # parsing/classification (confirmed live, 2026-10-02: 100-150ms per
    # message under real volume) never again shares a GIL with the
    # process that owns the real ThetaData WebSocket connection. Never
    # reaches the network beyond this machine.
    stream_processor_relay_host: str = Field(default="127.0.0.1")
    stream_processor_relay_port: int = Field(default=25601)
    # Signs session cookies (see backend/core/sessions.py). The random
    # default is fine for tests/a single dev process, but it's re-rolled
    # on every restart -- anything meant to keep users logged in across
    # restarts (Convexa, the production server) MUST set QLL_SESSION_SECRET
    # explicitly in its own .env, not rely on this default.
    session_secret: str = Field(default_factory=lambda: secrets.token_hex(32))

    @property
    def whale_alerts_bvc_active(self) -> bool:
        """Resolved value of whale_alerts_bvc_enabled: the explicit setting
        when given, else on for every provider except ThetaData."""
        if self.whale_alerts_bvc_enabled is not None:
            return self.whale_alerts_bvc_enabled
        return self.data_provider != "thetadata"


@lru_cache
def get_settings() -> Settings:
    return Settings()
