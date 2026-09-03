"""Feeds the chart's live price from a provider's Stock Trade Stream.

Additive, not a replacement: `RefreshUnderlyingSnapshotUseCase` (the REST
scheduler) remains the only source of truth for Gamma/GEX/OI and every
other derived metric — this use case only persists `MarketPrice` more
often than that scheduler's 30s cadence, so the chart's price source has
a fresher reading between cycles whenever the stream is actually
delivering ticks. If it isn't (provider has nothing to stream —
MockDataProvider — or a real stream that never connects/never receives a
tick for a symbol), nothing here writes anything, and the chart keeps
working exactly as it does today, off the scheduler's own writes.

NOT YET VERIFIED AGAINST A REAL THETADATA CONNECTION — see
ThetaUnderlyingTradeStream's docstring
(backend/adapters/providers/thetadata/provider.py) for why: ThetaData's
Stocks plan isn't active yet as of this PR.
"""

from __future__ import annotations

import time

from backend.domain.entities import MarketPrice, UnderlyingTradeEvent
from backend.domain.ports import IDataProvider, IStorage

# A liquid stock's Trade Stream can print many times per second, and
# IStorage.save_market_price() appends to an unbounded in-memory history
# (see InMemoryStorage._price_history — no eviction, by design, same as
# every other append-only history table this codebase already has) — so
# this debounces writes instead of persisting every tick. 1 second is
# still ~30x fresher than the REST scheduler's 30s cadence while keeping
# the growth rate a deliberate, bounded multiple of what already exists
# today, not an unbounded firehose.
MIN_WRITE_INTERVAL_SECONDS = 1.0


class StreamUnderlyingPriceUseCase:
    def __init__(
        self,
        provider: IDataProvider,
        storage: IStorage,
        min_write_interval_seconds: float = MIN_WRITE_INTERVAL_SECONDS,
    ) -> None:
        self._provider = provider
        self._storage = storage
        self._min_write_interval_seconds = min_write_interval_seconds
        self._last_written_at: dict[str, float] = {}

    async def run(self, underlying: str) -> None:
        """Consume the underlying trade stream for `underlying` until
        cancelled — runs forever for a real streaming provider (caller
        owns the task lifecycle, see core/underlying_price_stream.py),
        completes immediately for a provider with nothing to stream
        (MockDataProvider's stream_underlying_trades is an immediately-
        exhausted async generator)."""
        async for event in self._provider.stream_underlying_trades(underlying):
            self._maybe_persist(event)

    def _maybe_persist(self, event: UnderlyingTradeEvent) -> None:
        now = time.monotonic()
        last_written_at = self._last_written_at.get(event.symbol)
        if last_written_at is not None and now - last_written_at < self._min_write_interval_seconds:
            return
        self._last_written_at[event.symbol] = now
        self._storage.save_market_price(
            MarketPrice(
                symbol=event.symbol,
                as_of=event.as_of,
                price=event.price,
                # ThetaDataProvider's own MarketSnapshot.volume is already
                # a documented 0 for every symbol today (no live Stocks/
                # Indices share-volume subscription) — this doesn't
                # regress anything the REST scheduler already provides,
                # and accumulating a real cumulative volume from this
                # stream's own per-trade `size` is a natural follow-up,
                # deliberately out of this PR's price-only scope.
                volume=0,
            )
        )
