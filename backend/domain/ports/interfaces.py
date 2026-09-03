from __future__ import annotations

from datetime import date, datetime
from typing import AsyncIterator, Protocol

from backend.domain.entities import (
    DailyBar,
    DailyGammaReference,
    FlowEvent,
    GammaAggregate,
    GammaExposure,
    GammaFlip,
    MarketPrice,
    MarketSnapshot,
    MaxPain,
    OptionChain,
    QuoteEvent,
    ScreenerPreset,
    ScreenerPresetSettings,
    Underlying,
    UnderlyingTradeEvent,
    Walls,
    WhaleThreshold,
)


class IDataProvider(Protocol):
    def get_option_chain(self, underlying: str, expiration: date | None = None) -> OptionChain: ...
    def get_underlying_snapshot(self, underlying: str) -> MarketSnapshot: ...
    def get_daily_bars(self, underlying: str, days: int = 20) -> list[DailyBar]: ...
    def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]: ...
    # Added alongside stream_trades for Lee-Ready (StreamWhaleAlertsUseCase,
    # calculate_lee_ready.py) — the quote-rule needs the bid/ask prevailing
    # at each trade, which stream_trades alone never carries.
    def stream_quotes(self, underlying: str) -> AsyncIterator[QuoteEvent]: ...
    # The underlying's OWN live price (a Stock Trade Stream tick), not an
    # option contract's — distinct from stream_trades above, which is
    # options flow scoped by underlying. Feeds StreamUnderlyingPriceUseCase
    # (backend/domain/use_cases/stream_underlying_price.py), additively —
    # it persists MarketPrice more often than the REST scheduler's 30s
    # cadence, never replacing that scheduler as the source of truth for
    # anything else (Gamma/GEX/OI).
    def stream_underlying_trades(self, underlying: str) -> AsyncIterator[UnderlyingTradeEvent]: ...
    # Lifecycle hooks for providers backed by a persistent connection (e.g.
    # a streaming WebSocket) that must be opened/closed with the process,
    # not per-call. A no-op for providers with nothing to start (MockData
    # Provider) — kept on the port, not provider-specific, so `main.py`'s
    # lifespan and the container stay provider-agnostic (same reasoning
    # that already keeps UnderlyingRefreshScheduler unaware of which
    # concrete provider it's driving).
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class IGreeksCalculator(Protocol):
    def calculate(self, chain: OptionChain) -> OptionChain: ...


class IGammaExposureCalculator(Protocol):
    def calculate(self, chain: OptionChain) -> tuple[GammaExposure, ...]: ...


class IGammaAggregateCalculator(Protocol):
    def calculate(
        self, exposures: tuple[GammaExposure, ...], symbol: str, as_of: datetime
    ) -> GammaAggregate: ...


class IGammaFlipCalculator(Protocol):
    def calculate(self, aggregate: GammaAggregate) -> GammaFlip: ...


class IWallCalculator(Protocol):
    def calculate(self, aggregate: GammaAggregate) -> Walls: ...


class IMaxPainCalculator(Protocol):
    def calculate(self, chain: OptionChain) -> MaxPain: ...


class IStorage(Protocol):
    def list_underlyings(self) -> list[Underlying]: ...
    def save_whale_threshold(self, threshold: WhaleThreshold) -> None: ...
    def get_whale_thresholds(self) -> dict[str, WhaleThreshold]: ...
    def save_screener_preset_settings(
        self, preset: ScreenerPreset, settings: ScreenerPresetSettings
    ) -> None: ...
    def get_screener_preset_settings(
        self, preset: ScreenerPreset
    ) -> ScreenerPresetSettings | None: ...
    def save_chain_snapshot(self, chain: OptionChain) -> None: ...
    def get_latest_chain_snapshot(
        self, underlying: str, expiration: date | None = None
    ) -> OptionChain | None: ...
    def save_gamma_aggregate(self, gamma: GammaAggregate) -> None: ...
    def get_latest_gamma_aggregate(self, underlying: str) -> GammaAggregate | None: ...
    def get_gamma_history(
        self, underlying: str, start: datetime, end: datetime
    ) -> list[GammaAggregate]: ...
    def save_market_price(self, price: MarketPrice) -> None: ...
    def get_latest_price(self, underlying: str) -> MarketPrice | None: ...
    def get_price_history(
        self, underlying: str, start: datetime, end: datetime
    ) -> list[MarketPrice]: ...
    def save_flow_event(self, event: FlowEvent) -> None: ...
    def get_flow_events(
        self, underlying: str, since: datetime | None = None, limit: int = 100
    ) -> list[FlowEvent]: ...
    def get_recent_flow(self, underlying: str, limit: int = 20) -> list[FlowEvent]: ...
    def save_daily_gamma_reference(self, reference: DailyGammaReference) -> None: ...
    def get_daily_gamma_references(
        self, underlying: str, limit: int = 60
    ) -> list[DailyGammaReference]: ...
    def save_daily_bar(self, bar: DailyBar) -> None: ...
    def get_daily_bars(self, underlying: str, limit: int = 15) -> list[DailyBar]: ...


class INotificationService(Protocol):
    def notify(self, event: FlowEvent | GammaAggregate) -> None: ...


MarketDataProvider = IDataProvider
GreeksCalculator = IGreeksCalculator
Storage = IStorage
