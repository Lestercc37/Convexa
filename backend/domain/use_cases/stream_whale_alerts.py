"""Feeds WhaleAlertsEngine.process_trade() from a live provider.

Concurrently consumes a provider's Trade Stream and Quote Stream (both
IDataProvider port methods — never a concrete adapter import, same rule
every other use case in this package already follows) and keeps the
"last known bid/ask per contract" state Lee-Ready's quote rule needs.

No time-indexed quote history — just a plain dict, replaced whenever a
newer QuoteEvent for that occ_symbol arrives. "Vigente en ese instante"
on a live stream reduces to "most recently received," the same timestamp
precision the rest of this codebase already has for trades (FlowEvent.
as_of is stamped at local receipt time too, not a reconciled on-exchange
timestamp) — see LatestQuote's own docstring.

FIXED (2026-09-10, live production incident): `process_trade()` calls
`WhaleAlertsEngine._emit()`, which does a *synchronous* `IStorage.
save_whale_alert()` write (plain psycopg/SQLAlchemy, no async driver).
Calling that directly from `_consume_trades()` — a coroutine on the same
event loop `ThetaStreamHub`'s own read loop runs on — blocks market data
delivery for the whole process on every DB write, not just this
symbol's own stream. Confirmed live with a py-spy dump of the frozen
worker process, mid-incident, real market hours: the main thread was
inside `psycopg`'s own blocking wait, called from here. Same fix already
applied to `_reconcile()` in ThetaStreamHub and to `RefreshUnderlyingSnapshotUseCase.execute()`
in the REST scheduler — the write itself stays a plain synchronous
method (the domain layer doesn't need to know or care that one adapter's
implementation happens to be slow), and the call is offloaded to a
worker thread at the point where an async consumer invokes it.

FIXED AGAIN, same day: offloading alone still shared `asyncio.to_thread`'s
*default* executor with the REST scheduler's own concurrent symbol
refreshes and ThetaStreamHub's reconcile() -- confirmed live, real market
open, that the scheduler was actively running throughout the exact
minute a busy symbol's own trade queue filled and started dropping
messages, real cross-workload contention for one shared, undersized
pool. `executor` (see core/whale_alerts_stream.py, which owns its
lifecycle) is a dedicated `ThreadPoolExecutor`, sized to the number of
active symbols -- every symbol's own trade-consumer task always has an
uncontended thread available now, regardless of what the scheduler or
reconcile() are doing. Deliberately *not* `asyncio.to_thread()` itself
(that function always uses the default executor, with no parameter to
override it) -- `loop.run_in_executor()` is the documented way to submit
to a specific one; the `contextvars.copy_context()` + `ctx.run` wrapping
below reproduces `asyncio.to_thread()`'s own context-propagation
behavior exactly, so switching executors doesn't also silently change
that.

FIXED AGAIN, same day (SPY-specific): the dedicated executor removed
cross-workload contention but not the per-symbol ceiling -- each symbol's
own consumer task still only ever has ONE executor call in flight at a
time (it awaits each one before pulling the next trade off that symbol's
queue), so throughput per symbol is capped by (1 trade / process_trade
latency), regardless of pool size. Confirmed live, 2026-09-10, with
symbol-tagged queue instrumentation added to ThetaStreamHub specifically
to answer this: every CRITICAL drop that day (2,568 of them) was SPY's
own option-TRADE queue -- never SPX (the prior suspect), never any other
symbol, never the QUOTE queue. SPY's options trade on more strikes across
more exchanges at a far higher raw tick rate than SPX's fewer, chunkier
institutional prints (SPX's ~10x per-contract notional means the same
dollar exposure takes far fewer trades to express) -- a tick-RATE
problem, not a trade-SIZE one, which is exactly why the symbol historical
intuition pointed to for "whale-sized" activity (SPX) wasn't the one
actually saturating this queue.

HIGH_VOLUME_BATCHED_SYMBOLS + _consume_trades_batched below only change
SPY's own consumption: instead of one executor round-trip per trade
(paying the asyncio<->thread handoff cost per message), a time-boxed
micro-batch is drained from the stream first and classified in one
executor call, amortizing that cost over many trades. The other ~14
symbols keep the original one-trade-per-call path unchanged -- they've
never shown this problem even once across a full trading day of
symbol-tagged observation, so paying batching's inherent added latency
there would be pure cost with no corresponding benefit. If a different
symbol ever starts saturating its own queue the same way, the CRITICAL
log (now symbol-tagged) will say so by name, the same way it did for
SPY -- that's the trigger to revisit this list, not a scheduled review.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import AsyncIterator
from concurrent.futures import Executor

from backend.domain.entities import FlowEvent, LatestQuote
from backend.domain.ports import IDataProvider
from backend.domain.use_cases.flow import WhaleAlertsEngine

# See this module's docstring -- confirmed live, 2026-09-10, via
# symbol-tagged queue instrumentation, not assumed from this project's
# own history of suspecting SPX.
HIGH_VOLUME_BATCHED_SYMBOLS: frozenset[str] = frozenset({"SPY"})

# A batch flushes after this many seconds even if it hasn't reached
# BATCH_MAX_SIZE (so a quiet stretch on a batched symbol never stalls
# classification waiting for volume that isn't coming), or once it
# reaches BATCH_MAX_SIZE, whichever comes first. Both are a starting
# calibration against the two bursts that motivated this (39s/1,963
# drops and 2s/605 drops -- roughly 50-300 trades/sec) -- see this
# module's own docstring for the live-verification bar these need to
# clear before being trusted as final.
BATCH_WINDOW_SECONDS = 0.1
BATCH_MAX_SIZE = 50


class _BatchCollector:
    """Turns a bare `AsyncIterator[FlowEvent]` into windowed micro-batches
    -- see this module's own docstring for why SPY needs this.

    Deliberately never cancels a pending `stream.__anext__()` call just
    because a window elapsed: `stream_trades()`'s own shape (`while True:
    yield await queue.get()`, see the ThetaData adapter) means a
    cancelled `__anext__()` raises CancelledError *inside* that loop body
    -- uncaught, that propagates out of the async generator function
    entirely, which permanently closes it. Every subsequent `__anext__()`
    on the same (now-closed) generator then raises StopAsyncIteration
    immediately, regardless of whatever real trades were still queued up
    behind it -- silently ending that symbol's consumption forever, not
    just "missing one batch." Caught by this module's own test suite
    (a slow-trickle test expecting two separate flushed batches instead
    got one, with the second trade never delivered at all) before this
    ever reached production.

    The fix: a pending next-item task is created at most once and kept
    across calls to `collect()` (in `self._pending`) -- `asyncio.shield()`
    protects it from `wait_for`'s own cancellation-on-timeout, so a
    window elapsing just means "stop waiting for now," not "abandon this
    read." The same still-running task is picked up again on the next
    `collect()` call rather than restarted. Only `aclose()` (called when
    the consumer itself is shutting down, not on a per-window timeout)
    actually cancels it."""

    def __init__(self, stream: AsyncIterator[FlowEvent]) -> None:
        self._stream = stream
        self._pending: asyncio.Task[FlowEvent] | None = None

    def _next_task(self) -> asyncio.Task[FlowEvent]:
        if self._pending is None:
            self._pending = asyncio.ensure_future(self._stream.__anext__())
        return self._pending

    async def collect(self, window_seconds: float, max_size: int) -> list[FlowEvent] | None:
        """One micro-batch: waits indefinitely for the first event (there's
        nothing to batch yet), then anchors the window to that moment --
        not reset per event -- so a steady trickle still flushes every
        `window_seconds` rather than stalling for a gap that never comes.
        Returns None once the stream is exhausted with nothing collected,
        the caller's cue to stop consuming (a real streaming provider
        never returns this way; only a finite one, as in tests, does)."""
        batch: list[FlowEvent] = []
        deadline: float | None = None
        loop = asyncio.get_running_loop()
        while len(batch) < max_size:
            task = self._next_task()
            timeout = None if deadline is None else deadline - loop.time()
            if timeout is not None and timeout <= 0:
                break
            try:
                event = (
                    await asyncio.shield(task)
                    if timeout is None
                    else await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
                )
            except TimeoutError:
                break
            except StopAsyncIteration:
                self._pending = None
                break
            self._pending = None
            batch.append(event)
            if deadline is None:
                deadline = loop.time() + window_seconds
        return batch or None

    async def aclose(self) -> None:
        """Cancel any in-flight next-item task -- only called when the
        consumer itself is stopping (see _consume_trades_batched's own
        `finally`), never on a mere per-window timeout (see `collect`'s
        own docstring for why that case deliberately keeps it alive)."""
        if self._pending is None:
            return
        self._pending.cancel()
        try:
            await self._pending
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        self._pending = None


class StreamWhaleAlertsUseCase:
    def __init__(
        self,
        provider: IDataProvider,
        engine: WhaleAlertsEngine,
        executor: Executor | None = None,
    ) -> None:
        self._provider = provider
        self._engine = engine
        # None falls back to asyncio's own default shared executor --
        # same behavior as before this fix, for any caller (tests
        # included) that doesn't have a dedicated one to pass.
        self._executor = executor
        self._latest_quotes: dict[str, LatestQuote] = {}

    async def run(self, underlying: str) -> None:
        """Consume both streams for `underlying` until cancelled.

        Runs forever for a real streaming provider — callers own the
        task lifecycle (see core/whale_alerts_stream.py) and cancel it
        on shutdown, same pattern as ThetaTradeStream/ThetaQuoteStream's
        own `stop()`. Completes immediately for a provider with nothing
        to stream (e.g. MockDataProvider — both of its stream methods
        are an immediately-exhausted async generator).
        """
        await asyncio.gather(
            self._consume_quotes(underlying),
            self._consume_trades(underlying),
        )

    async def _consume_quotes(self, underlying: str) -> None:
        async for quote_event in self._provider.stream_quotes(underlying):
            self._latest_quotes[quote_event.occ_symbol] = LatestQuote(
                bid=quote_event.bid,
                ask=quote_event.ask,
                as_of=quote_event.as_of,
            )

    async def _consume_trades(self, underlying: str) -> None:
        """Dispatches to the batched or single-message path by symbol --
        see this module's own docstring for why only
        HIGH_VOLUME_BATCHED_SYMBOLS need the former."""
        if underlying.upper() in HIGH_VOLUME_BATCHED_SYMBOLS:
            await self._consume_trades_batched(underlying)
        else:
            await self._consume_trades_single(underlying)

    async def _consume_trades_single(self, underlying: str) -> None:
        async for trade_event in self._provider.stream_trades(underlying):
            quote = self._latest_quotes.get(trade_event.occ_symbol)
            # See this module's own docstring -- process_trade() can do a
            # blocking synchronous DB write (WhaleAlertsEngine._emit);
            # offloaded to self._executor (a dedicated pool, not the
            # default shared one) so it can never stall the shared event
            # loop ThetaStreamHub's own read loop runs on, and never
            # queues behind the REST scheduler's or reconcile()'s own
            # unrelated work either.
            loop = asyncio.get_running_loop()
            ctx = contextvars.copy_context()
            call = functools.partial(ctx.run, self._engine.process_trade, trade_event, quote)
            await loop.run_in_executor(self._executor, call)

    async def _consume_trades_batched(self, underlying: str) -> None:
        """See this module's own docstring -- SPY's own trade rate can
        outpace one-executor-call-per-trade even on an uncontended
        dedicated thread; this amortizes the executor round-trip over a
        time-boxed micro-batch instead. Each event's quote is looked up
        at the same moment `_consume_trades_single` would have (as it's
        drained off the stream), not deferred to batch-processing time --
        identical "last known quote when this trade arrived" semantics,
        just collected into a list first."""
        stream = self._provider.stream_trades(underlying).__aiter__()
        collector = _BatchCollector(stream)
        loop = asyncio.get_running_loop()
        try:
            while True:
                batch = await collector.collect(BATCH_WINDOW_SECONDS, BATCH_MAX_SIZE)
                if batch is None:
                    return
                pairs = [(event, self._latest_quotes.get(event.occ_symbol)) for event in batch]
                ctx = contextvars.copy_context()
                call = functools.partial(ctx.run, self._engine.process_trade_batch, pairs)
                await loop.run_in_executor(self._executor, call)
        finally:
            await collector.aclose()
