from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from backend.domain.entities import (
    DailyBar,
    DailyGammaReference,
    FlowEvent,
    GammaAggregate,
    GammaHeatmap,
    LargeTradePrint,
    MarketPrice,
    OptionChain,
    OptionQuoteMinute,
)
from backend.domain.ports import IStorage
from backend.domain.use_cases.flow import SymbolFlowPressure, WhaleAlert


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
        self.option_quote_minutes: list[OptionQuoteMinute] = []
        self.large_trade_prints: list[LargeTradePrint] = []

    async def get_latest_gamma_aggregate(
        self, underlying: str, view: str = "structural"
    ) -> GammaAggregate | None:
        return self._storage.get_latest_gamma_aggregate(underlying, view=view)

    async def get_latest_price(self, underlying: str) -> MarketPrice | None:
        return self._storage.get_latest_price(underlying)

    async def save_market_price(self, price: MarketPrice) -> None:
        self._storage.save_market_price(price)

    async def save_option_quote_minutes(self, rows: list[OptionQuoteMinute]) -> None:
        # No table behind the in-memory/sqlite storages: kept in memory so tests can read it back.
        self.option_quote_minutes.extend(rows)

    async def get_volume_corrections(self, occ_symbols: list[str]) -> dict[str, int]:
        return self._storage.get_volume_corrections(occ_symbols)

    async def save_large_trade_prints(self, rows: list[LargeTradePrint]) -> None:
        self.large_trade_prints.extend(rows)

    async def get_price_history(
        self, underlying: str, start: datetime, end: datetime
    ) -> list[MarketPrice]:
        return self._storage.get_price_history(underlying, start, end)

    async def get_gamma_heatmap(
        self, underlying: str, view: str, metric: str, start: datetime, end: datetime, bucket_seconds: int
    ) -> GammaHeatmap:
        # The in-memory/sqlite storages keep no per-strike history, so there is nothing to bin: an empty matrix.
        return GammaHeatmap(
            symbol=underlying.upper(), view=view, metric=metric, bucket_seconds=bucket_seconds, strikes=(), columns=()
        )

    async def get_latest_chain_snapshot(
        self, underlying: str, expiration: date | None = None
    ) -> OptionChain | None:
        return self._storage.get_latest_chain_snapshot(underlying, expiration)

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

    async def get_symbol_flow_pressure(self, underlying: str) -> SymbolFlowPressure | None:
        return self._storage.get_symbol_flow_pressure(underlying)

    async def get_future_price_anchor(self, symbol: str, session_date: date) -> Decimal | None:
        return self._storage.get_future_price_anchor(symbol, session_date)
