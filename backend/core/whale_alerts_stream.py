from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from backend.core.container import Container
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases import StreamWhaleAlertsUseCase

logger = logging.getLogger(__name__)

# Same exponential-backoff shape and values already established for every
# other ThetaData stream reconnect in this codebase (ThetaStreamHub's own
# _run(), core/underlying_price_stream.py's identical supervisor,
# core/price_notifications.py's own Postgres LISTEN reconnect) --
# deliberately the same numbers, not a new convention for this one
# supervisor.
RECONNECT_BASE_DELAY_SECONDS = 2
RECONNECT_MAX_DELAY_SECONDS = 60

# Fix (2026-09-10): process_trade() (offloaded via StreamWhaleAlertsUseCase's
# own executor -- see its docstring) used to run on asyncio.to_thread()'s
# *default* executor, shared process-wide with the REST scheduler's own
# concurrent symbol refreshes (up to THETADATA_MAX_CONCURRENT_REQUESTS=8
# in flight) and ThetaStreamHub's reconcile(). Confirmed live, real market
# open: the scheduler was demonstrably running continuously (a cycle every
# ~30s, each holding threads for ~4-5s) throughout the exact minute SPX's
# own trade queue filled and started dropping messages (4,265 confirmed
# CRITICAL drops) -- real, measured cross-workload contention for a
# 16-worker pool (12 CPUs + 4, this machine), not just a plausible theory.
# A dedicated executor, sized to the number of active symbols rather than
# CPU count (this workload is I/O-bound -- a BVC/Lee-Ready classification
# plus an occasional Postgres write, not CPU-bound work), guarantees every
# symbol's own trade-consumer task always has an uncontended thread
# available, regardless of what the scheduler or reconcile() are doing at
# that moment. Does NOT by itself raise the ceiling on how fast a single
# very busy symbol's own sequential pipeline can drain (each symbol still
# processes its own trades one at a time) -- if a burst that large recurs
# even without cross-workload contention, that's the confirmed signal to
# revisit batching instead, not a reason to guess at it now.
WHALE_ALERTS_EXECUTOR_MAX_WORKERS = len(ACTIVE_UNDERLYINGS)


class WhaleAlertsStreamManager:
    """Runs StreamWhaleAlertsUseCase.run() concurrently for every active
    underlying, for the life of the process.

    Same start()/stop() task-lifecycle pattern as UnderlyingRefreshScheduler,
    but one long-lived task per symbol instead of one periodic cycle — each
    symbol's Trade Stream + Quote Stream subscriptions never return on
    their own (unlike a scheduled REST poll), so they can't be run
    sequentially the way the scheduler's cycle runs symbols one after
    another.

    A no-op in practice under MockDataProvider (its stream_trades/
    stream_quotes are both an immediately-exhausted async generator, so
    each symbol's task just completes right away) — deliberately
    provider-agnostic, same reasoning that already keeps
    UnderlyingRefreshScheduler unaware of which concrete provider it's
    driving.

    One symbol's stream failing is caught per-task, logged, AND
    RESTARTED with backoff — never crashing the process or another
    symbol's task, and never left dead forever. This used to just log
    and give up on that symbol's Whale Alerts permanently, the exact
    same class of bug UnderlyingPriceStreamManager already had fixed for
    the chart's own per-symbol consumer (see its own docstring) --
    confirmed missing here too during the 2026-09-09/10 ThetaStreamHub
    investigation, fixed the same way rather than reintroducing it. A
    clean return from run() (MockDataProvider's exhausted generator, or
    any provider whose stream just ends on its own) is NOT retried --
    only an actual exception is, so this stays a true no-op under
    MockDataProvider/tests exactly as before.
    """

    def __init__(self, container: Container) -> None:
        self._container = container
        self._tasks: list[asyncio.Task[None]] = []
        self._executor: ThreadPoolExecutor | None = None

    def start(self) -> None:
        if self._tasks:
            return
        self._executor = ThreadPoolExecutor(
            max_workers=WHALE_ALERTS_EXECUTOR_MAX_WORKERS,
            thread_name_prefix="whale-alerts",
        )
        use_case = StreamWhaleAlertsUseCase(
            provider=self._container.market_data_provider,
            engine=self._container.whale_alerts_engine,
            executor=self._executor,
        )
        self._tasks = [
            asyncio.create_task(self._run_symbol(use_case, underlying.symbol))
            for underlying in ACTIVE_UNDERLYINGS
        ]

    async def _run_symbol(self, use_case: StreamWhaleAlertsUseCase, symbol: str) -> None:
        delay = RECONNECT_BASE_DELAY_SECONDS
        while True:
            try:
                await use_case.run(symbol)
                # A clean return (not an exception) -- nothing to
                # restart; matches MockDataProvider's exhausted
                # generator and any provider whose stream simply ends.
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Whale Alerts trade-stream consumer failed for %s, restarting in %ss",
                    symbol,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        if self._executor is not None:
            # wait=False, cancel_futures=True -- same "fast, non-blocking
            # shutdown" contract as the task cancellation above, not a new
            # convention. A process_trade() call already mid-flight in a
            # worker thread when this runs keeps running to completion
            # regardless (ThreadPoolExecutor has no way to interrupt a
            # running thread) -- this only drops whatever hadn't started
            # yet, same as CancelledError already does for the tasks
            # themselves.
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
