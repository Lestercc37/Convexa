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
    (e.g. a real ThetaData REST connection, which does real HTTP I/O) can't
    stall the event loop or the rest of the API while a cycle is in flight
    — MockDataProvider is fast enough that this makes no observable
    difference for it, but the scheduler is deliberately independent of
    which provider is behind it.

    Symbols are dispatched concurrently (`asyncio.gather`), not one at a
    time — measured against the real ATR-widened chain widths (2026-09
    investigation): a fully sequential cycle across 11 symbols took
    ~30 seconds, essentially the same as `interval_seconds` itself, so the
    effective refresh cadence was closer to ~60s (`_run` sleeps *after*
    a cycle finishes) than the intended 30s. Real REST-call safety against
    ThetaData's actual account-wide concurrency cap is the
    `threading.Semaphore` already living inside `ThetaDataProvider`
    (`THETADATA_MAX_CONCURRENT_REQUESTS`) — it's the single real chokepoint
    every REST call passes through regardless of which symbol's worker
    thread issued it, so the scheduler dispatching all symbols at once is
    safe by construction and doesn't need its own separate throttle.
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
        outcomes = await asyncio.gather(*(self._refresh_symbol(symbol) for symbol in symbols))
        failed = [symbol for symbol, succeeded in zip(symbols, outcomes, strict=True) if not succeeded]
        succeeded_count = len(symbols) - len(failed)
        if failed:
            logger.warning(
                "Scheduler cycle finished: %d succeeded, %d failed (%s)",
                succeeded_count,
                len(failed),
                ", ".join(failed),
            )
        else:
            logger.info("Scheduler cycle finished: %d succeeded, 0 failed", succeeded_count)

    async def _refresh_symbol(self, symbol: str) -> bool:
        """Refreshes one symbol, reporting success/failure instead of
        raising — every symbol is dispatched via `_run_cycle`'s
        `asyncio.gather` regardless of how others finish, so one symbol's
        exception must never propagate out and cancel the rest."""
        try:
            await asyncio.to_thread(
                self._container.refresh_underlying_snapshot_use_case.execute, symbol
            )
            return True
        except Exception:
            logger.exception("Scheduler cycle failed for %s", symbol)
            return False
