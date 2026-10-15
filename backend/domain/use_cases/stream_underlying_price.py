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

Gated on is_market_open (see persist_if_due): ThetaData's real Trade
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

# Sale conditions whose price is not a market price and that the consolidated tape does not let set the
# last price (ThetaData's trade-condition table, read 2026-10-14; confirmed on real IWM prints of that
# morning: every >= 8 bps spike on the IWM chart was a FINRA/Nasdaq TRF print, exchange 57, with condition
# 115 or 3, all at sub-penny prices well away from the quote). None of them is ever drawn as the price;
# the volume is untouched (stream ticks carry the scheduler's volume forward, never their own size).
#   2 OUT_OF_SEQ     3 AVG_PRC (NYSE/AMEX)   4 AVG_PRC_NASDAQ   59 VWAP
#   96 DERIVATIVE (derivatively priced)      105/114/124 contingent trades
#   106 STOPPED_IM (valid trade, does not update last)   109 IMPLIED   115 ODD_LOT (size 1-99)
NON_LAST_SALE_CONDITIONS: frozenset[int] = frozenset({2, 3, 4, 59, 96, 105, 106, 109, 114, 115, 124})


def sets_last_price(event: UnderlyingTradeEvent) -> bool:
    """False for a print whose condition code cannot set the last price; True when the code is not
    in the list or the message carried none (unknown means keep, as before this filter)."""
    return event.condition not in NON_LAST_SALE_CONDITIONS


# A liquid stock's Trade Stream can print many times per second, and
# IStorage.save_market_price() appends to an unbounded in-memory history
# (see InMemoryStorage._price_history — no eviction, by design, same as
# every other append-only history table this codebase already has) — so
# this debounces writes instead of persisting every tick. 1 second is
# still ~30x fresher than the REST scheduler's 30s cadence while keeping
# the growth rate a deliberate, bounded multiple of what already exists
# today, not an unbounded firehose.
MIN_WRITE_INTERVAL_SECONDS = 1.0

# Trial (2026-09-22, explicit user request): these 5 -- SPX/NDX/SPY/QQQ/
# IWM, the most-watched symbols -- get every real trade tick persisted
# and pushed to the chart almost immediately (debounce: see
# TICK_LEVEL_MIN_WRITE_INTERVAL_SECONDS below), instead of coalesced to at
# most one write per second like everything else. Safe
# to trial now specifically because the risk that made a blanket
# "every tick, every symbol" policy risky before (this process's event
# loop contending with the REST scheduler's own concurrent per-symbol
# work for the GIL) is gone -- the scheduler no longer shares this
# process at all (see backend/scheduler_worker.py's own docstring). The
# underlying-price queue's own bounded maxsize + drop-oldest policy
# (ThetaStreamHub._dispatch_dropping, UNDERLYING_QUEUE_MAXSIZE) is the
# real backpressure valve regardless of this setting, so 0.0 here still
# can't produce an unbounded write rate even under a genuine burst.
# Scale to more symbols gradually once this trial confirms Postgres/the
# WebSocket push to the chart hold up under real tick rates for these 5.
TICK_LEVEL_SYMBOLS: frozenset[str] = frozenset({"SPX", "NDX", "SPY", "QQQ", "IWM"})
# 2026-10-12 (explicit user decision): 0.25 s instead of 0.0. The zero was only "safe" in
# practice because every write was preceded by a get_latest_price() whose old query took 400+ ms
# (~12 calls/s in total, ~1 write/s per symbol); with that query on the index the same code path can write
# as fast as ticks arrive (SPY alone prints dozens of trades a second -> millions of
# market_snapshots rows a day). 0.25 s caps a tick-level symbol at 4 rows/s (~20 rows/s for the five
# plus ~8 for the rest, ~650k rows/day in all) while still being ~4x finer than the ~1/s it effectively got before.
TICK_LEVEL_MIN_WRITE_INTERVAL_SECONDS = 0.25


class StreamUnderlyingPriceUseCase:
    def __init__(
        self,
        provider: IDataProvider,
        storage: IAsyncMarketReadStorage,
        min_write_interval_seconds: float = MIN_WRITE_INTERVAL_SECONDS,
        tick_level_symbols: frozenset[str] = TICK_LEVEL_SYMBOLS,
        tick_level_min_write_interval_seconds: float = TICK_LEVEL_MIN_WRITE_INTERVAL_SECONDS,
    ) -> None:
        self._provider = provider
        self._storage = storage
        self._min_write_interval_seconds = min_write_interval_seconds
        self._tick_level_symbols = tick_level_symbols
        self._tick_level_min_write_interval_seconds = tick_level_min_write_interval_seconds
        self._last_written_at: dict[str, float] = {}

    def _interval_for(self, symbol: str) -> float:
        if symbol.upper() in self._tick_level_symbols:
            return self._tick_level_min_write_interval_seconds
        return self._min_write_interval_seconds

    async def run(self, underlying: str) -> None:
        """Consume the underlying trade stream for `underlying` until
        cancelled — runs forever for a real streaming provider (caller
        owns the task lifecycle, see core/underlying_price_stream.py),
        completes immediately for a provider with nothing to stream
        (MockDataProvider's stream_underlying_trades is an immediately-
        exhausted async generator)."""
        async for event in self._provider.stream_underlying_trades(underlying):
            await self.persist_if_due(event)

    async def persist_if_due(self, event: UnderlyingTradeEvent) -> None:
        """Public (was _maybe_persist until 2026-10-02): the exact same
        market-hours gate + per-symbol debounce + volume-carry-forward
        logic, now also called directly by
        backend/stream_processor_worker.py for each UnderlyingTradeEvent
        it classifies -- that process has no provider.
        stream_underlying_trades() of its own to iterate (it never owns a
        live stream, see that module's own docstring), it just has one
        event at a time already in hand. run() above is unchanged for
        worker.py's own in-process fallback path."""
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
        # Before the debounce: an ignored print must not use up the write window of the next real one.
        if not sets_last_price(event):
            return
        now = time.monotonic()
        last_written_at = self._last_written_at.get(event.symbol)
        if last_written_at is not None and now - last_written_at < self._interval_for(event.symbol):
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
