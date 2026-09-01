from __future__ import annotations

import asyncio
import logging

from backend.core.container import Container
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases import StreamWhaleAlertsUseCase

logger = logging.getLogger(__name__)


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
        try:
            await use_case.run(symbol)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Whale Alerts trade-stream consumer failed for %s", symbol)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
