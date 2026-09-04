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

CONFIRMED LIVE against a real Theta Terminal, market open (2026-09-03)
— see ThetaUnderlyingTradeStream's own docstring
(backend/adapters/providers/thetadata/provider.py) for the confirmed
message shapes and a real, still-open gap found in that verification
(option trades sharing an underlying's root symbol currently aren't
filtered out) that can corrupt the price this use case persists for an
affected symbol until that gap is fixed.

Gated on is_market_open (see _maybe_persist): ThetaData's real Trade
Stream keeps printing outside 09:30-16:00 ET (extended hours), and
persisting one of those ticks was the one path that put an
extended-hours point on the chart, since dashboard.tsx has no filter
of its own on what it polls. Confirmed live, 2026-09: a tick as late as
17:11 ET reached storage before this gate existed.

`storage` takes `IAsyncMarketReadStorage`, not `IStorage` -- this used
to call plain synchronous `IStorage.save_market_price` directly
(unawaited, no thread) from inside `run()`'s async loop, blocking the
event loop on every persisted tick. Confirmed live, 2026-09: a real,
independent contributor to the same /gamma and /market latency the
scheduler's own threadpool contention was already diagnosed for --
`AsyncPostgreSQLStorage`'s `save_market_price` (added for this fix)
reuses the same async engine that fix already established rather than
opening a third way to reach Postgres.
"""

from __future__ import annotations

import time

from backend.domain.entities import MarketPrice, UnderlyingTradeEvent
from backend.domain.ports import IAsyncMarketReadStorage, IDataProvider
from backend.domain.use_cases.market_hours import is_market_open

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
        storage: IAsyncMarketReadStorage,
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
            await self._maybe_persist(event)

    async def _maybe_persist(self, event: UnderlyingTradeEvent) -> None:
        # Nothing gated this stream on market hours -- confirmed live,
        # 2026-09: a real tick with as_of past 16:00 ET still got written,
        # and dashboard.tsx appends every MarketPrice.as_of it polls onto
        # the chart's pricePoints with no filter of its own, so an
        # extended-hours tick reaching storage always reached the chart.
        # The chart is meant to show only the regular 09:30-16:00 ET
        # session -- reusing the same is_market_open the REST scheduler
        # already gates on, not a new check.
        if not is_market_open(event.as_of):
            return
        now = time.monotonic()
        last_written_at = self._last_written_at.get(event.symbol)
        if last_written_at is not None and now - last_written_at < self._min_write_interval_seconds:
            return
        self._last_written_at[event.symbol] = now
        # save_market_price() replaces the whole stored row, not just a
        # `price` field (confirmed in both InMemoryStorage — a plain dict
        # assignment — and PostgresqlStorage — a new market_snapshots row
        # that becomes "latest" by time — see get_latest_price() in each),
        # and MarketPrice.volume is a required field with no None/omit
        # option. So this carries the existing volume forward instead of
        # writing a value of its own: the REST scheduler
        # (get_underlying_snapshot(), see ThetaDataProvider) stays the
        # only thing that ever sets a real volume — the stream ticks
        # merely repeat it forward between scheduler cycles so a stream
        # write (as frequent as once a second) can never reset it back to
        # 0. Falls back to 0 only if nothing has been written for this
        # symbol at all yet — same starting value this already had.
        previous = await self._storage.get_latest_price(event.symbol)
        await self._storage.save_market_price(
            MarketPrice(
                symbol=event.symbol,
                as_of=event.as_of,
                price=event.price,
                volume=previous.volume if previous is not None else 0,
            )
        )
