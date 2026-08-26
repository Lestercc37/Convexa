from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from backend.core.container import Container
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases import is_market_open

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 30.0


class UnderlyingRefreshScheduler:
    """Refreshes gamma/market data for every active underlying on a timer.

    Runs one cycle every `interval_seconds`, but only while `is_market_open`
    — see that function's docstring for the known holiday-calendar gap
    (weekday + time-of-day only, no exchange holiday calendar). Started and
    stopped from the FastAPI lifespan (`backend/main.py`) as a single
    `asyncio.Task` that lives for the process's lifetime; each symbol runs
    in a worker thread (`asyncio.to_thread`) via
    `RefreshUnderlyingSnapshotUseCase` so a slow/blocking data provider
    (e.g. a future real FlashAlpha connection, which does real HTTP I/O)
    can't stall the event loop or the rest of the API while a cycle is in
    flight — MockDataProvider is fast enough that this makes no observable
    difference today, but the scheduler is deliberately independent of
    which provider is behind it.
    """

    def __init__(
        self,
        container: Container,
        interval_seconds: float = REFRESH_INTERVAL_SECONDS,
    ) -> None:
        self._container = container
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            if is_market_open(datetime.now(UTC)):
                await self._run_cycle()
            await asyncio.sleep(self._interval_seconds)

    async def _run_cycle(self) -> None:
        symbols = [underlying.symbol for underlying in ACTIVE_UNDERLYINGS]
        logger.info("Scheduler cycle starting for %d symbols", len(symbols))
        failed: list[str] = []
        for symbol in symbols:
            try:
                await asyncio.to_thread(
                    self._container.refresh_underlying_snapshot_use_case.execute, symbol
                )
            except Exception:
                failed.append(symbol)
                logger.exception("Scheduler cycle failed for %s", symbol)
        succeeded = len(symbols) - len(failed)
        if failed:
            logger.warning(
                "Scheduler cycle finished: %d succeeded, %d failed (%s)",
                succeeded,
                len(failed),
                ", ".join(failed),
            )
        else:
            logger.info("Scheduler cycle finished: %d succeeded, 0 failed", succeeded)
