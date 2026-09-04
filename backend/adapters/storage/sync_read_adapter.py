from __future__ import annotations

from datetime import datetime

from backend.domain.entities import (
    DailyBar,
    DailyGammaReference,
    FlowEvent,
    GammaAggregate,
    MarketPrice,
    OptionChain,
)
from backend.domain.ports import IStorage
from backend.domain.use_cases.flow import WhaleAlert


class SyncStorageAsyncReadAdapter:
    """Async-shaped wrapper around a synchronous `IStorage`, so the
    `/gamma/{symbol}` and `/market/{symbol}` routes can `await` the same
    interface (`AsyncPostgreSQLStorage`'s) regardless of which storage
    backend the container built.

    Exists for `InMemoryStorage` (unit/API tests -- see
    tests/conftest.py's sqlite-backed `DATABASE_URL`, which the async
    engine can't share data with, since `InMemoryStorage` is a plain
    Python dict, not a SQL backend at all) and for `TimescaleStorage`
    (a still-unimplemented scaffold). Every call here is a fast in-
    process dict/list lookup, not real network I/O, so wrapping it in
    `async def` with no actual `await` inside doesn't block the event
    loop the way a real synchronous Postgres call would -- there's
    nothing to yield to.
    """

    def __init__(self, storage: IStorage) -> None:
        self._storage = storage

    async def get_latest_gamma_aggregate(self, underlying: str) -> GammaAggregate | None:
        return self._storage.get_latest_gamma_aggregate(underlying)

    async def get_latest_price(self, underlying: str) -> MarketPrice | None:
        return self._storage.get_latest_price(underlying)

    async def save_market_price(self, price: MarketPrice) -> None:
        self._storage.save_market_price(price)

    async def get_price_history(
        self, underlying: str, start: datetime, end: datetime
    ) -> list[MarketPrice]:
        return self._storage.get_price_history(underlying, start, end)

    async def get_latest_chain_snapshot(self, underlying: str) -> OptionChain | None:
        return self._storage.get_latest_chain_snapshot(underlying)

    async def get_daily_bars(self, underlying: str, limit: int = 15) -> list[DailyBar]:
        return self._storage.get_daily_bars(underlying, limit=limit)

    async def get_recent_flow(self, underlying: str, limit: int = 20) -> list[FlowEvent]:
        return self._storage.get_recent_flow(underlying, limit=limit)

    async def get_daily_gamma_references(
        self, underlying: str, limit: int = 60
    ) -> list[DailyGammaReference]:
        return self._storage.get_daily_gamma_references(underlying, limit=limit)

    async def get_recent_whale_alerts(self, underlying: str, limit: int = 100) -> list[WhaleAlert]:
        return self._storage.get_recent_whale_alerts(underlying, limit=limit)
