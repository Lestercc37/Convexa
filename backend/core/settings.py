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
    # The 10-second fast gamma layer (backend/fast_gamma_worker.py, docs/diseno-capa-rapida-gamma-2026-10-19.txt): re-evaluates the last stored chain's
    # gamma / vanna / charm at the live spot and writes only gamma_fast_items. OFF by default; QLL_FAST_GAMMA_ENABLED=true and a start of the worker turns it on,
    # and the reverse turns it off, without reverting any code. Needs migration 0047.
    fast_gamma_enabled: bool = Field(default=False)
    fast_gamma_symbols: str = Field(default="SPX,NDX,SPY,QQQ")
    fast_gamma_interval_seconds: float = Field(default=10.0, ge=2.0)
    # "mock" (default, no external dependency) or "thetadata" (real Theta
    # Terminal v3, local REST + WebSocket) — switchable without a code
    # change specifically so a real-data incident can be diagnosed by
    # falling back to Mock without spending real ThetaData quota.
    data_provider: Literal["mock", "thetadata"] = Field(default="mock")
    thetadata_rest_url: str = Field(default="http://localhost:25503")
    thetadata_ws_url: str = Field(default="ws://127.0.0.1:25520/v1/events")
    # The Worker's periodic reconcile() (ThetaStreamHub, every 20 min): one REST
    # ohlc call per registered contract (1,200-1,800 calls per pass, 13-25 s, up to
    # 8 request slots at once) comparing REST volume with a stream counter that the
    # Worker no longer keeps (the stream processor owns the counters), so it logs
    # "stream=0" mismatches and nothing reads its result. It also competes with the
    # scheduler and the API for the account-wide request slots (it starved the
    # scheduler on 2026-10-06 10:21 and 10-07 13:50) and shares the Terminal's Java
    # process with the live stream. OFF by default; set QLL_THETADATA_RECONCILE_ENABLED=true
    # and restart the Worker to bring it back without reverting any code.
    thetadata_reconcile_enabled: bool = Field(default=False)
    # Gamma / vanna / charm of an in-the-money contract with no readable time value (its IV is noise-prone) use the IV of its OTM twin (same strike and expiration, the other right).
    # Measured 2026-10-19 on stored chains: the levels (flip, walls, ABS) do not move, the vanna / charm / net sums move by 10-500 %. ON by default since Lester's yes on 2026-10-22; the REST cycle (provider)
    # and the 10-second fast layer apply the same rule. QLL_THETADATA_ITM_IV_FROM_TWIN=false and a restart of the scheduler worker and the fast gamma worker turn it off. See docs/hm_iv_noise_levels.py.
    thetadata_itm_iv_from_twin: bool = Field(default=True)
    # The stream processor keeps the last changed bid/ask of every contract per minute in
    # option_quote_minutes (migration 0040). QLL_OPTION_QUOTE_MINUTES_ENABLED=false and a restart of
    # ConvexaStreamProcessor switches it off without reverting any code.
    option_quote_minutes_enabled: bool = Field(default=True)
    # The stream processor keeps the large SPY/QQQ prints (over $1M) with their exchange and condition in large_trade_prints
    # (migration 0043). QLL_LARGE_TRADE_PRINTS_ENABLED=false and a restart of ConvexaStreamProcessor switches it off.
    large_trade_prints_enabled: bool = Field(default=True)
    # The stream processor keeps the signed option delta flow per ET minute (minute_option_flow, migration 0048): simple trades classified against the prevailing bid/ask, sized with the delta of the
    # latest chain snapshot. QLL_MINUTE_OPTION_FLOW_ENABLED=false and a restart of ConvexaStreamProcessor switches it off without reverting any code.
    minute_option_flow_enabled: bool = Field(default=True)
    minute_option_flow_symbols: str = Field(default="SPX,NDX,SPY,QQQ,IWM,NVDA")
    # Audit sample of the minute flow, OFF by default: QLL_MINUTE_FLOW_AUDIT_SYMBOLS=SPY,SPX and QLL_MINUTE_FLOW_AUDIT_WINDOW=10:15-10:18 (ET, end exclusive) keep every option trade of those
    # underlyings inside the window, with the bid/ask used and the outcome, in logs/minute_flow_audit_YYYY-MM-DD.ndjson, to check the panel trade by trade.
    minute_flow_audit_symbols: str = Field(default="")
    minute_flow_audit_window: str = Field(default="")
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
    # Whether the Whale Alerts engine records, per alert, how much of its
    # premium traded under each OPRA trade-condition code (whale_alerts.
    # condition_premium). Capture only: no alert, net-pressure figure or
    # volume is computed differently because of it. Kill switch for the
    # per-trade accumulation: QLL_WHALE_ALERTS_STORE_CONDITIONS=false.
    whale_alerts_store_conditions: bool = Field(default=True)
    # Alerts from the open: UNUSUAL / WHALE from 2 finalized minutes and SUSTAINED_FLOW from 3 minutes with a prorated minimum, instead of waiting 5 / 15 minutes
    # (WhaleAlertsEngine._MIN_PREVIOUS_BUCKETS_PARTIAL). Kill switch: QLL_WHALE_ALERTS_PARTIAL_WINDOWS=false.
    whale_alerts_partial_windows: bool = Field(default=True)
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
