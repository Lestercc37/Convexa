from __future__ import annotations

from datetime import date, datetime
from typing import AsyncIterator, Protocol

from backend.domain.entities import (
    FlowEvent,
    GammaAggregate,
    GammaExposure,
    GammaFlip,
    Walls,
    MarketPrice,
    MarketSnapshot,
    MaxPain,
    OptionChain,
    Underlying,
)


class IDataProvider(Protocol):
    def get_option_chain(self, underlying: str, expiration: date | None = None) -> OptionChain: ...
    def get_underlying_snapshot(self, underlying: str) -> MarketSnapshot: ...
    def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]: ...


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
    def save_flow_event(self, event: FlowEvent) -> None: ...
    def get_flow_events(
        self, underlying: str, since: datetime | None = None, limit: int = 100
    ) -> list[FlowEvent]: ...
    def get_recent_flow(self, underlying: str, limit: int = 20) -> list[FlowEvent]: ...


class INotificationService(Protocol):
    def notify(self, event: FlowEvent | GammaAggregate) -> None: ...


MarketDataProvider = IDataProvider
GreeksCalculator = IGreeksCalculator
Storage = IStorage
