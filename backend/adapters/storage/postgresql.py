from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.domain.entities import FlowEvent, GammaAggregate, MarketPrice, OptionChain, Underlying


class PostgreSQLStorage:
    """PostgreSQL/TimescaleDB storage adapter scaffold for IStorage."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    def list_underlyings(self) -> list[Underlying]:
        raise NotImplementedError

    def save_chain_snapshot(self, chain: OptionChain) -> None:
        raise NotImplementedError

    def get_latest_chain_snapshot(
        self, underlying: str, expiration: date | None = None
    ) -> OptionChain | None:
        raise NotImplementedError

    def save_gamma_aggregate(self, gamma: GammaAggregate) -> None:
        raise NotImplementedError

    def get_latest_gamma_aggregate(self, underlying: str) -> GammaAggregate | None:
        raise NotImplementedError

    def get_gamma_history(
        self, underlying: str, start: datetime, end: datetime
    ) -> list[GammaAggregate]:
        raise NotImplementedError

    def save_market_price(self, price: MarketPrice) -> None:
        raise NotImplementedError

    def get_latest_price(self, underlying: str) -> MarketPrice | None:
        raise NotImplementedError

    def save_flow_event(self, event: FlowEvent) -> None:
        raise NotImplementedError

    def get_flow_events(
        self, underlying: str, since: datetime | None = None, limit: int = 100
    ) -> list[FlowEvent]:
        raise NotImplementedError

    def get_recent_flow(self, underlying: str, limit: int = 20) -> list[FlowEvent]:
        raise NotImplementedError
