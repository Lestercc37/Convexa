"""Standalone entrypoint for Convexa's 3 background systems --
UnderlyingRefreshScheduler (the 30s REST cycle), WhaleAlertsStreamManager,
and UnderlyingPriceStreamManager. No HTTP server here; backend/main.py's
FastAPI app no longer starts these itself (see its own lifespan docstring
for exactly why -- GIL/threadpool contention with /gamma and /market,
confirmed live and fixed piecemeal before this split, per the approved
process-split design).

This process owns the ONE ThetaDataProvider instance that actually opens
the 3 WebSocket connections (Trade/Quote/Underlying-Trade streams) and
runs continuously -- the API process's own ThetaDataProvider instance
(built independently by container.py) never does. Both instances share
the same real ThetaData account concurrency limit through the
Postgres-backed theta_request_slots table (see request_slots.py), not a
process-local threading.Semaphore, so running two processes doesn't risk
exceeding it.

Run with:
    python -m backend.worker

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
from backend.core.scheduler import UnderlyingRefreshScheduler
from backend.core.underlying_price_stream import UnderlyingPriceStreamManager
from backend.core.whale_alerts_stream import WhaleAlertsStreamManager

logger = logging.getLogger(__name__)


async def run() -> None:
    container = build_container()
    configure_logging(container.settings)
    logger.info("Starting %s worker", container.settings.app_name)

    if not container.settings.enable_scheduler:
        # Same kill switch backend/main.py's lifespan used to gate on --
        # now this process's own, since it's the only one left running
        # any of the 3 systems it would have controlled.
        logger.warning("enable_scheduler is False -- worker has nothing to start, exiting")
        return

    await container.market_data_provider.start()
    scheduler = UnderlyingRefreshScheduler(container)
    whale_alerts_stream = WhaleAlertsStreamManager(container)
    underlying_price_stream = UnderlyingPriceStreamManager(container)
    scheduler.start()
    whale_alerts_stream.start()
    underlying_price_stream.start()
    logger.info("Worker running: scheduler, whale-alerts stream, underlying-price stream")

    try:
        # Runs forever -- Ctrl+C (KeyboardInterrupt) or the process being
        # killed is how this process is meant to stop, same as any other
        # long-lived server process.
        await asyncio.Event().wait()
    finally:
        logger.info("Stopping %s worker", container.settings.app_name)
        await scheduler.stop()
        await whale_alerts_stream.stop()
        await underlying_price_stream.stop()
        await container.market_data_provider.stop()
        if container.storage_engine is not None:
            container.storage_engine.dispose()
        await container.database_engine.dispose()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
