"""Standalone entrypoint for UnderlyingRefreshScheduler (the ~60-75s REST
cycle: Gamma/GEX, walls, Max Pain, OI, daily bars, Whale Alerts' own
volume-delta detection -- everything RefreshUnderlyingSnapshotUseCase's
6-step pipeline drives, for every active symbol).

Split out of backend/worker.py, 2026-09-22: confirmed live that this
cycle's own concurrent per-symbol REST/JSON/object-construction work
(asyncio.to_thread, up to 15 symbols at once, heavier since SPX/NDX's
near-the-money width grew 4x for the Gamma Flip fix) was starving that
process's ThetaStreamHub event loop of GIL time badly enough to produce
a real WebSocket reconnect storm -- 65 reconnects in one 60-minute
window, confirmed by timestamp correlation between "one loop iteration
took Xms" warnings and this same cycle's own REST activity, then
confirmed again by ThetaData support's own read of the report: "slow
consumer... has nothing to do with TD's ws." Separate OS processes have
separate GILs -- the one fix that actually removes this contention,
not just relocates it (a dedicated thread pool, tried for Whale Alerts'
own stream consumer back in 2026-09-10, only isolates *scheduling*
onto the asyncio event loop; every thread in a process still shares that
one process's GIL).

Deliberately never calls `await container.market_data_provider.start()`
-- same reasoning as backend/main.py's own lifespan (see its docstring):
that's the one place that opens ThetaDataProvider's WebSocket stream and
does a real REST contract-discovery burst at startup, neither of which
this process needs. This process's own ThetaDataProvider instance only
ever needs the plain REST methods (get_option_chain / get_daily_bars /
get_underlying_snapshot / get_market_holidays), coordinated through the
same Postgres-backed theta_request_slots semaphore every other
ThetaDataProvider instance in this codebase already shares (see
request_slots.py) -- running a third process doesn't risk exceeding the
account's real concurrency limit.

Two real, confirmed consequences of never starting the stream here,
the first the same as backend/main.py's own:

1. get_option_chain()'s `volume` field comes from `self._stream.
   cumulative_volume(occ_symbol)`, which reads 0 for every contract
   when the stream was never started. RefreshUnderlyingSnapshotUseCase.
   execute() -- unlike the rarely-hit paths main.py's own comment
   accepts this for -- feeds that chain into WhaleAlertsEngine.
   process(chain), whose detection depends on real volume, so this is
   NOT an accepted gap here: see _merge_cumulative_volume
   (backend/domain/use_cases/refresh_snapshot.py).
2. This process's own WhaleAlertsEngine instance never receives a
   single process_trade() call either (only backend/worker.py's own,
   separate instance does, fed by the real trade stream), so its net
   client flow pressure accumulation is always empty here.

StreamStateExporter (backend/core/stream_state_export.py, run by
backend/worker.py, the process that DOES own the live stream and the
WhaleAlertsEngine instance process_trade() actually feeds) backfills
both of these for this process from Postgres, on its own independent
cadence -- this process no longer reads or writes either one directly.

Run with:
    python -m backend.scheduler_worker

Also run backend/worker.py (as its own separate process) -- without it,
this process's own chains report 0 volume for everything until
CumulativeVolumeExporter's first export lands, and nothing streams
live prices/whale alerts at all. See that module's own docstring.
"""

from __future__ import annotations

import asyncio
import logging

from backend.core.container import build_container
from backend.core.logging import configure_logging
from backend.core.scheduler import UnderlyingRefreshScheduler

logger = logging.getLogger(__name__)


async def run() -> None:
    container = build_container()
    configure_logging(container.settings, log_file="logs/scheduler_worker.log")
    logger.info("Starting %s scheduler worker", container.settings.app_name)

    if not container.settings.enable_scheduler:
        # Same kill switch backend/worker.py's own process checks --
        # both independently, so it still disables all background work
        # when set, exactly as before this file split in two.
        logger.warning(
            "enable_scheduler is False -- scheduler worker has nothing to start, exiting"
        )
        return

    scheduler = UnderlyingRefreshScheduler(container)
    scheduler.start()
    logger.info("Scheduler worker running")

    try:
        # Runs forever -- Ctrl+C (KeyboardInterrupt) or the process being
        # killed is how this process is meant to stop, same as any other
        # long-lived server process.
        await asyncio.Event().wait()
    finally:
        logger.info("Stopping %s scheduler worker", container.settings.app_name)
        await scheduler.stop()
        # Never started (see this module's own docstring) -- still
        # called, same as backend/main.py's own lifespan: every
        # ThetaDataProvider stop() no-ops on a stream that was never
        # started, but this is what actually closes the underlying
        # httpx.Client at shutdown -- skipping it would leak that
        # connection pool.
        await container.market_data_provider.stop()
        if container.storage_engine is not None:
            container.storage_engine.dispose()
        if container.whale_alerts_storage_engine is not None:
            container.whale_alerts_storage_engine.dispose()
        await container.database_engine.dispose()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
