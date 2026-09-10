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
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from concurrent.futures import Executor

from backend.domain.entities import LatestQuote
from backend.domain.ports import IDataProvider
from backend.domain.use_cases.flow import WhaleAlertsEngine


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
