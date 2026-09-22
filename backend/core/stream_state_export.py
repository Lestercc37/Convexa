from __future__ import annotations

import asyncio
import logging

from backend.core.container import Container
from backend.domain.underlyings import ACTIVE_UNDERLYINGS

logger = logging.getLogger(__name__)

# How often the process that actually owns a live ThetaData trade stream
# (this process, backend/worker.py) exports its own in-memory state --
# ThetaStreamHub's _cumulative_volume and WhaleAlertsEngine's own
# per-symbol net flow accumulation -- to Postgres, so
# backend/scheduler_worker.py (split out, 2026-09-22, to stop the
# scheduler's own REST/JSON/object-construction work contending with
# THIS process's event loop for the GIL) can still report real volume
# and net client flow pressure instead of silently 0/nothing for
# everything. See RefreshUnderlyingSnapshotUseCase's own
# _merge_cumulative_volume comment (volume) and its execute()'s own
# comment (flow pressure) for why both matter -- WhaleAlerts detection
# depends on the former, the dashboard's own flow-pressure reading on
# the latter. 15s: frequent enough that the scheduler-only process's own
# ~60-75s cycle never reads anything more than one export interval
# stale, cheap enough (two small batched writes) to not matter at that
# cadence.
EXPORT_INTERVAL_SECONDS = 15


class StreamStateExporter:
    """Periodically snapshots this process's own live-stream-fed state
    to Postgres, for a stream-less process (backend/scheduler_worker.py)
    to read back -- see this module's own comment.

    A genuine no-op under MockDataProvider (cumulative_volumes() always
    returns {}, and nothing ever calls WhaleAlertsEngine.process_trade()
    to populate its own flow accumulation either, so every export writes
    nothing) and under a ThetaDataProvider instance whose stream was
    never started (same reason) -- safe to construct and start()
    unconditionally, same provider-agnostic stance as every other
    manager in this package."""

    def __init__(self, container: Container) -> None:
        self._container = container
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(EXPORT_INTERVAL_SECONDS)
            try:
                await self._export_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("StreamStateExporter: export failed, will retry next interval")

    async def _export_once(self) -> None:
        volumes = self._container.market_data_provider.cumulative_volumes()
        if volumes:
            await asyncio.to_thread(self._container.storage.save_cumulative_volumes, volumes)

        engine = self._container.whale_alerts_engine
        for underlying in ACTIVE_UNDERLYINGS:
            flow_pressure = engine.symbol_flow(underlying.symbol)
            if flow_pressure is not None:
                await asyncio.to_thread(self._container.storage.save_symbol_flow_pressure, flow_pressure)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
