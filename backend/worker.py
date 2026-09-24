"""Standalone entrypoint for Convexa's ThetaData stream-owning systems --
WhaleAlertsRelayPublisher, UnderlyingPriceStreamManager, and
StreamStateExporter. No HTTP server here; backend/main.py's FastAPI
app no longer starts these itself (see its own lifespan docstring for
exactly why -- GIL/threadpool contention with /gamma and /market,
confirmed live and fixed piecemeal before this split, per the approved
process-split design).

Whale-alerts' own CPU-bound classification (Lee-Ready side
classification, bucketing, threshold comparisons) does NOT run in this
process (2026-09-24 change) -- it moved to backend/whale_alerts_worker.py,
its own OS process with its own GIL. Confirmed live that running it here,
even offloaded to a dedicated thread pool, still shared this process's
GIL with ThetaStreamHub's own WebSocket read loop closely enough to
starve it under real trade volume: 17 reconnects and ~70,000 dropped
trade messages in under an hour, each preceded by ~10s of queue-
saturation CRITICAL logs. This process now only relays raw trade/quote
events to that other process (WhaleAlertsRelayPublisher, see
backend/core/whale_alerts_relay.py) -- cheap serialize-and-enqueue work
that runs directly on this process's own event loop, nothing that
competes for the GIL the WebSocket read loop needs.

Consequence for StreamStateExporter (still started below, unchanged):
its own whale_alerts_engine.symbol_flow() export half is now a
permanent no-op HERE -- this process's own WhaleAlertsEngine instance
never receives a single process_trade() call anymore, since that only
happens in backend/whale_alerts_worker.py's own separate instance now
(which runs its own StreamStateExporter for exactly this reason -- see
that module's own comment). Only this process's cumulative_volumes()
export half still does real work here.

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
from backend.core.whale_alerts_relay import WhaleAlertsRelayPublisher, WhaleAlertsRelayServer

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
    whale_alerts_relay_server = WhaleAlertsRelayServer(
        container.settings.whale_alerts_relay_host, container.settings.whale_alerts_relay_port
    )
    await whale_alerts_relay_server.start()
    whale_alerts_relay_publisher = WhaleAlertsRelayPublisher(
        container.market_data_provider, whale_alerts_relay_server
    )
    underlying_price_stream = UnderlyingPriceStreamManager(container)
    stream_state_exporter = StreamStateExporter(container)
    whale_alerts_relay_publisher.start()
    underlying_price_stream.start()
    stream_state_exporter.start()
    logger.info(
        "Worker running: whale-alerts relay, underlying-price stream, state exporter -- "
        "also run backend.whale_alerts_worker for whale alerts to actually process"
    )

    try:
        # Runs forever -- Ctrl+C (KeyboardInterrupt) or the process being
        # killed is how this process is meant to stop, same as any other
        # long-lived server process.
        await asyncio.Event().wait()
    finally:
        logger.info("Stopping %s worker", container.settings.app_name)
        await whale_alerts_relay_publisher.stop()
        await whale_alerts_relay_server.stop()
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
