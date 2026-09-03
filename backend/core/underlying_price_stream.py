from __future__ import annotations

import asyncio
import logging

from backend.core.container import Container
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases import StreamUnderlyingPriceUseCase

logger = logging.getLogger(__name__)


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
    concrete provider they're driving. One symbol's stream failing (or
    ThetaData's Stocks plan simply not being active yet — see
    ThetaUnderlyingTradeStream's docstring) is caught per-task and
    logged, never crashing the process or another symbol's task — the
    dashboard keeps working off the REST scheduler's own writes exactly
    as it does today.
    """

    def __init__(self, container: Container) -> None:
        self._container = container
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        if self._tasks:
            return
        use_case = StreamUnderlyingPriceUseCase(
            provider=self._container.market_data_provider,
            storage=self._container.storage,
        )
        self._tasks = [
            asyncio.create_task(self._run_symbol(use_case, underlying.symbol))
            for underlying in ACTIVE_UNDERLYINGS
        ]

    async def _run_symbol(self, use_case: StreamUnderlyingPriceUseCase, symbol: str) -> None:
        try:
            await use_case.run(symbol)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Underlying price stream consumer failed for %s", symbol)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
