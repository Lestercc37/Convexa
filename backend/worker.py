"""Standalone entrypoint for Convexa's ThetaData stream-owning systems --
the raw WebSocket read loop, and (fallback-mode only, see below)
UnderlyingPriceStreamManager/StreamStateExporter. No HTTP server here;
backend/main.py's FastAPI app no longer starts these itself (see its own
lifespan docstring for exactly why -- GIL/threadpool contention with
/gamma and /market, confirmed live and fixed piecemeal before this split,
per the approved process-split design).

QUOTE/TRADE parsing/classification does NOT run in this process when a
stream processor is connected (2026-10-02, v2 -- see
backend/stream_processor_worker.py's own docstring and docs/stream-
processor-split-postmortem-2026-10-02.md for the full history, including
v1's collapse under real volume). This process's job in the normal case
is narrow: own the one real ThetaData WebSocket connection, do the cheap
per-message work that genuinely has to stay here (watchdog "last seen"
bookkeeping, routing), and relay the raw frame on, one-way, to whichever
stream processor is connected -- backend/core/stream_processor_relay.py.
That process reaches every real destination directly (Postgres for
price/volume, its own WhaleAlertsRelayServer instance) instead of
reporting back here.

Fallback mode (no stream processor connected -- StreamProcessorRelayServer
.has_client is False): ThetaStreamHub._process_messages handles QUOTE/
TRADE itself, in-process, exactly as before any of this split existed.
In that mode, THIS process's own UnderlyingPriceStreamManager/
StreamStateExporter (still started below, unchanged) do real work again,
reading from ThetaStreamHub's own subscriber queues -- so core market
data (the chart's tick-level price boost) keeps working even with the
stream processor down. Whale Alerts has NO fallback in this process at
all (see the comment at the call site below for why: a fixed TCP port
conflict with the stream processor's own WhaleAlertsRelayServer instance,
confirmed live the same day this was first deployed) -- it requires
backend/stream_processor_worker.py running, full stop, the same
acceptable-degradation story this codebase already has for
whale_alerts_worker.py's own downtime (see that module's own docstring),
now one level up the chain. The REST scheduler's own 30s cadence is the
one thing none of this ever touches.

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
without it, nothing refreshes Gamma/GEX/OI/daily bars, ever. Also run
backend/stream_processor_worker.py for QUOTE/TRADE parsing to happen off
this process (recommended, not required -- see fallback mode above), and
backend/whale_alerts_worker.py for Whale Alerts to actually process. See
each module's own docstring.

For hot-reload during development (this script has no equivalent to
uvicorn's own --reload), wrap it with `watchfiles`, already a transitive
dependency of uvicorn's `[standard]` extra:
    watchfiles "python -m backend.worker" backend
"""

from __future__ import annotations

import asyncio
import logging

from backend.adapters.providers.thetadata.provider import ThetaDataProvider
from backend.core.container import build_container
from backend.core.logging import configure_logging
from backend.core.stream_processor_relay import StreamProcessorRelayServer
from backend.core.stream_state_export import StreamStateExporter
from backend.core.underlying_price_stream import UnderlyingPriceStreamManager

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

    # Constructed and started BEFORE market_data_provider.start() so
    # set_processor_relay() is wired in before _process_messages (part
    # of that start()) ever runs its first iteration -- no window where
    # a message is handled before the Hub even knows the relay exists.
    # has_client is simply False until backend/stream_processor_worker.py
    # actually connects, which _process_messages already treats as "fall
    # back to in-process handling" (see StreamProcessorRelayServer's own
    # docstring on graceful degradation), so this ordering never blocks
    # startup waiting for that other process.
    #
    # set_processor_relay is ThetaDataProvider-specific (not part of
    # IDataProvider -- every other provider, e.g. MockDataProvider under
    # QLL_DATA_PROVIDER=mock, has no WS stream to offload parsing from in
    # the first place), so this stays guarded, same pattern
    # backfill_daily_gamma_reference.py's own isinstance check already
    # established for the same reason.
    stream_processor_relay: StreamProcessorRelayServer | None = None
    if isinstance(container.market_data_provider, ThetaDataProvider):
        stream_processor_relay = StreamProcessorRelayServer(
            container.settings.stream_processor_relay_host,
            container.settings.stream_processor_relay_port,
        )
        await stream_processor_relay.start()
        container.market_data_provider.set_processor_relay(stream_processor_relay)

    await container.market_data_provider.start()
    # Fallback-mode-only from here (see this module's own docstring):
    # these two do real work only while no stream processor is connected
    # and ThetaStreamHub._process_messages is handling underlying TRADE
    # in-process. Harmless, correct no-ops the rest of the time -- same
    # "safe to run unconditionally, no-ops cleanly" stance this codebase
    # already takes with MockDataProvider everywhere else (see
    # save_cumulative_volumes' own empty-dict guard for why
    # StreamStateExporter specifically can never overwrite the stream
    # processor's own, fresher export with stale/empty data).
    #
    # Deliberately NOT a WhaleAlertsRelayServer/Publisher pair here too
    # (there was one, briefly, during this same deploy -- removed within
    # the hour): that class binds a fixed TCP port
    # (whale_alerts_relay_host:port), and backend/stream_processor_worker.py
    # already binds the exact same port for its own, primary instance.
    # Two processes cannot both listen on one port -- confirmed live,
    # 2026-10-02, OSError 10048 crash-looping ConvexaStreamProcessor on
    # every start attempt. Whale Alerts has no fallback in this process
    # as a result: it requires backend/stream_processor_worker.py running,
    # full stop, same accepted story as whale_alerts_worker.py's own
    # downtime (see that module's own docstring), just one level up the
    # chain now. Core market data (price, via UnderlyingPriceStreamManager
    # below) is unaffected either way.
    underlying_price_stream = UnderlyingPriceStreamManager(container)
    stream_state_exporter = StreamStateExporter(container)
    underlying_price_stream.start()
    stream_state_exporter.start()
    logger.info(
        "Worker running: stream processor relay (raw frames out, one-way), "
        "underlying-price stream/state exporter (fallback-mode only) -- also run "
        "backend.stream_processor_worker for QUOTE/TRADE parsing (and Whale Alerts, "
        "which has no fallback in this process) to happen off this process, and "
        "backend.whale_alerts_worker for whale alerts to actually process"
    )

    try:
        # Runs forever -- Ctrl+C (KeyboardInterrupt) or the process being
        # killed is how this process is meant to stop, same as any other
        # long-lived server process.
        await asyncio.Event().wait()
    finally:
        logger.info("Stopping %s worker", container.settings.app_name)
        await underlying_price_stream.stop()
        await stream_state_exporter.stop()
        await container.market_data_provider.stop()
        if stream_processor_relay is not None:
            await stream_processor_relay.stop()
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
