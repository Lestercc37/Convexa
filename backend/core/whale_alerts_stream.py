from __future__ import annotations

import asyncio
import logging

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

    def start(self) -> None:
        if self._tasks:
            return
        use_case = StreamWhaleAlertsUseCase(
            provider=self._container.market_data_provider,
            engine=self._container.whale_alerts_engine,
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
