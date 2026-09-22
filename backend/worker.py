"""Standalone entrypoint for Convexa's ThetaData stream-owning systems --
WhaleAlertsStreamManager, UnderlyingPriceStreamManager, and
StreamStateExporter. No HTTP server here; backend/main.py's FastAPI
app no longer starts these itself (see its own lifespan docstring for
exactly why -- GIL/threadpool contention with /gamma and /market,
confirmed live and fixed piecemeal before this split, per the approved
process-split design).

The REST scheduler (UnderlyingRefreshScheduler) used to run in this same
process too, until 2026-09-22: confirmed live that its own concurrent
per-symbol REST/JSON/object-construction work (asyncio.to_thread, up to
15 symbols at once, heavier since SPX/NDX's near-the-money width grew 4x)
was starving THIS process's own event loop of GIL time badly enough to
produce a real WebSocket reconnect storm -- ThetaData support's own read
of our report: "slow consumer... has nothing to do with TD's ws." See
backend/scheduler_worker.py, the new process it moved to.

This process owns the ONE ThetaDataProvider instance that actually opens
the WebSocket stream (ThetaStreamHub, consolidating what used to be 3
separate connections) and runs continuously -- the API process's own
ThetaDataProvider instance (built independently by container.py) never
does, and neither does scheduler_worker.py's. All three share the same
real ThetaData account concurrency limit through the Postgres-backed
theta_request_slots table (see request_slots.py), not a process-local
threading.Semaphore, so running multiple processes doesn't risk
exceeding it.

Run with:
    python -m backend.worker

Also run backend/scheduler_worker.py (as its own separate process) --
without it, nothing refreshes Gamma/GEX/OI/daily bars, ever. See that
module's own docstring.

For hot-reload during development (this script has no equivalent to
uvicorn's own --reload), wrap it with `watchfiles`, already a transitive
dependency of uvicorn's `[standard]` extra:
    watchfiles "python -m backend.worker" backend
"""

from __future__ import annotations

import asyncio
import logging

from backend.core.container import build_container
from backend.core.logging import configure_logging
from backend.core.stream_state_export import StreamStateExporter
from backend.core.underlying_price_stream import UnderlyingPriceStreamManager
from backend.core.whale_alerts_stream import WhaleAlertsStreamManager

logger = logging.getLogger(__name__)


async def run() -> None:
    container = build_container()
    configure_logging(container.settings, log_file="logs/worker.log")
    logger.info("Starting %s worker", container.settings.app_name)

    if not container.settings.enable_scheduler:
        # Same kill switch this flag has always gated background work
        # on (backend/main.py's lifespan, before the original process
        # split; this process and scheduler_worker.py since) -- both
        # processes check it independently, so it still disables all
        # background work when set, exactly as before this file split
        # in two.
        logger.warning("enable_scheduler is False -- worker has nothing to start, exiting")
        return

    await container.market_data_provider.start()
    whale_alerts_stream = WhaleAlertsStreamManager(container)
    underlying_price_stream = UnderlyingPriceStreamManager(container)
    stream_state_exporter = StreamStateExporter(container)
    whale_alerts_stream.start()
    underlying_price_stream.start()
    stream_state_exporter.start()
    logger.info("Worker running: whale-alerts stream, underlying-price stream, state exporter")

    try:
        # Runs forever -- Ctrl+C (KeyboardInterrupt) or the process being
        # killed is how this process is meant to stop, same as any other
        # long-lived server process.
        await asyncio.Event().wait()
    finally:
        logger.info("Stopping %s worker", container.settings.app_name)
        await whale_alerts_stream.stop()
        await underlying_price_stream.stop()
        await stream_state_exporter.stop()
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
