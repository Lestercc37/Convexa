from __future__ import annotations

import asyncio
import logging

from backend.core.container import Container
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases import StreamUnderlyingPriceUseCase

logger = logging.getLogger(__name__)

# Same exponential-backoff shape and values already established for
# every other ThetaData WebSocket reconnect in this codebase
# (ThetaTradeStream/ThetaQuoteStream/ThetaUnderlyingTradeStream in
# adapters/providers/thetadata/provider.py, and
# core/price_notifications.py's own Postgres LISTEN reconnect) --
# deliberately the same numbers, not a new convention for this one
# supervisor.
RECONNECT_BASE_DELAY_SECONDS = 2
RECONNECT_MAX_DELAY_SECONDS = 60


class UnderlyingPriceStreamManager:
    """Runs StreamUnderlyingPriceUseCase.run() concurrently for every
    active underlying, for the life of the process.

    Same start()/stop() task-lifecycle pattern as
    WhaleAlertsStreamManager/UnderlyingRefreshScheduler — one long-lived
    task per symbol, since each symbol's Stock Trade Stream subscription
    never returns on its own.

    A no-op in practice under MockDataProvider (its
    stream_underlying_trades is an immediately-exhausted async
    generator, so each symbol's task just completes right away) —
    deliberately provider-agnostic, same reasoning that already keeps
    UnderlyingRefreshScheduler/WhaleAlertsStreamManager unaware of which
    concrete provider they're driving.

    One symbol's stream failing (or ThetaData's Stocks plan simply not
    being active yet — see ThetaUnderlyingTradeStream's docstring) is
    caught per-task, logged, AND RESTARTED with backoff — never crashing
    the process or another symbol's task. This used to just log and give
    up on that symbol forever, silently falling back to the REST
    scheduler's own 30s writes for the rest of the process's life —
    confirmed live, 2026-09 (real market open): a Worker running since
    the day before had every symbol's real-time push dead this way,
    undetected, for hours. A clean return from run() (MockDataProvider's
    exhausted generator, or any provider whose stream just ends on its
    own) is NOT retried -- only an actual exception is, so this stays a
    true no-op under MockDataProvider/tests exactly as before.
    """

    def __init__(self, container: Container) -> None:
        self._container = container
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        if self._tasks:
            return
        use_case = StreamUnderlyingPriceUseCase(
            provider=self._container.market_data_provider,
            storage=self._container.async_market_storage,
        )
        self._tasks = [
            asyncio.create_task(self._run_symbol(use_case, underlying.symbol))
            for underlying in ACTIVE_UNDERLYINGS
        ]

    async def _run_symbol(self, use_case: StreamUnderlyingPriceUseCase, symbol: str) -> None:
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
                    "Underlying price stream consumer failed for %s, restarting in %ss",
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
