"""Standalone entrypoint for whale-alerts trade classification -- the
CPU-bound half of what used to run inside backend/worker.py itself
(WhaleAlertsStreamManager: Lee-Ready side classification, per-contract
bucketing, threshold comparisons, alert persistence).

Split out 2026-09-24: confirmed live that running this in the same
process as ThetaStreamHub's own WebSocket read loop -- even offloaded to
a dedicated thread pool, which only ever removed *cross-workload*
contention with the REST scheduler, never the ceiling on how fast this
workload itself could run -- still shared that process's one GIL with
the read loop closely enough to starve it under real trade volume: 17
WebSocket reconnects and ~70,000 dropped trade messages in under an
hour, each disconnect preceded by ~10s of queue-saturation CRITICAL
logs. A separate OS process is the one thing that actually removes this
-- its own GIL, no longer sharing anything with the process that must
stay responsive to ThetaData.

This process never opens a connection to ThetaData itself -- Theta
Terminal allows exactly one WebSocket connection account-wide (see
ThetaStreamHub's own docstring), already held by backend/worker.py.
Instead it connects to that process's local-only relay
(WhaleAlertsRelayServer, backend/core/whale_alerts_relay.py) over a
plain TCP socket on this same machine, receives the same trade/quote
events worker.py's own ThetaDataProvider produces, and re-exposes them
through RelayDataProvider -- an IDataProvider-shaped adapter satisfying
exactly the two methods WhaleAlertsStreamManager needs
(stream_trades/stream_quotes). WhaleAlertsStreamManager itself,
StreamWhaleAlertsUseCase, and WhaleAlertsEngine are all reused completely
unchanged from before this split -- only where they get their events
from moved, not the already-proven classification pipeline itself.

Deliberately never calls `await container.market_data_provider.start()`
-- same reasoning as scheduler_worker.py's own lifespan (see its
docstring): this process's own ThetaDataProvider instance (built by the
same build_container() every process uses) is never actually used for
anything; the real streaming happens through the relay client
(`relay_provider` below) instead. `stop()` is still called at shutdown
on that unused instance purely to close its underlying httpx.Client
connection pool cleanly, exactly as scheduler_worker.py already does for
the identical reason.

If this process is down, worker.py's own relay publisher just has
nobody to forward events to (a documented no-op -- see
WhaleAlertsRelayServer's own docstring) and whale-alerts coverage pauses
until this process comes back; market data delivery (charts, GEX, real-
time price) is completely unaffected, which is the entire point of this
split.

Run with:
    python -m backend.whale_alerts_worker

Also run backend/worker.py (as its own separate process) -- without it,
this process has no relay server to connect to, and never receives a
single trade or quote. See that module's own docstring.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import time
from collections.abc import Callable, Iterable

from backend.core.container import build_container
from backend.core.logging import configure_logging
from backend.core.stream_state_export import StreamStateExporter
from backend.core.whale_alerts_relay import RelayDataProvider
from backend.core.whale_alerts_stream import WhaleAlertsStreamManager
from backend.domain.underlyings import ACTIVE_UNDERLYINGS

logger = logging.getLogger(__name__)

# How often the count of messages ignored for symbols this process does not
# track is summarized (one INFO line, only when there is something to report).
IGNORED_SYMBOLS_SUMMARY_INTERVAL_SECONDS = 60.0
# Bucket for messages whose "symbol" is missing, None or not a string.
MISSING_SYMBOL_KEY = "<missing>"


class _ActiveSymbolsRelayDataProvider(RelayDataProvider):
    """RelayDataProvider that drops, before decoding and queueing, every message
    for a symbol outside ACTIVE_UNDERLYINGS.

    The stream processor publishes every option quote/trade it receives from
    the Terminal, whatever the symbol, and the Terminal keeps streaming symbols
    removed from ACTIVE_UNDERLYINGS until their subscriptions are dropped on its
    side (2026-10-06: TSLA, META, AMZN, GOOGL, AAPL, MSFT and DIA, ~11% of what
    the relay carries at the open). RelayDataProvider._dispatch creates a queue
    for ANY symbol it sees and only the active ones have a consumer, so the
    others would fill their 20,000-message queues in 1.5-8 minutes and then log
    one CRITICAL line per message. This class filters on the symbol the
    processor already resolved to the underlying (SPXW -> SPX, NDXP -> NDX,
    VIXW -> VIX, see stream_parsing._underlying_symbol_for_root) -- the same key
    WhaleAlertsStreamManager consumes by -- and only counts what it drops.

    The set of symbols is read from ACTIVE_UNDERLYINGS when the provider is
    built, never a hand-copied list, so a symbol added to that list is tracked
    here automatically. Nothing in whale_alerts_relay.py (queue size, drop log)
    is changed.
    """

    def __init__(
        self,
        host: str,
        port: int,
        active_symbols: Iterable[str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(host, port)
        self._active_symbols: frozenset[str] = frozenset(
            active_symbols
            if active_symbols is not None
            else (underlying.symbol for underlying in ACTIVE_UNDERLYINGS)
        )
        self._monotonic = monotonic
        self._last_summary_at = monotonic()
        # Since the process started, and since the last summary line.
        self.ignored_by_symbol: collections.Counter[str] = collections.Counter()
        self._ignored_since_summary: collections.Counter[str] = collections.Counter()

    @property
    def active_symbols(self) -> frozenset[str]:
        return self._active_symbols

    def _dispatch(self, payload: dict[str, object]) -> None:
        symbol = payload.get("symbol") if isinstance(payload, dict) else None
        if not isinstance(symbol, str) or symbol not in self._active_symbols:
            self._note_ignored(symbol if isinstance(symbol, str) else MISSING_SYMBOL_KEY)
            return
        super()._dispatch(payload)

    def _note_ignored(self, symbol: str) -> None:
        self.ignored_by_symbol[symbol] += 1
        self._ignored_since_summary[symbol] += 1
        now = self._monotonic()
        if now - self._last_summary_at < IGNORED_SYMBOLS_SUMMARY_INTERVAL_SECONDS:
            return
        window = now - self._last_summary_at
        total = sum(self._ignored_since_summary.values())
        top = ", ".join(
            f"{name}={count}" for name, count in self._ignored_since_summary.most_common(10)
        )
        logger.info(
            "Whale alerts relay client: ignored %d messages in %.0fs for symbols this process does "
            "not track (%s); %d since start",
            total,
            window,
            top,
            sum(self.ignored_by_symbol.values()),
        )
        self._ignored_since_summary.clear()
        self._last_summary_at = now


def _build_relay_provider(host: str, port: int) -> RelayDataProvider:
    return _ActiveSymbolsRelayDataProvider(host, port)


async def run() -> None:
    container = build_container()
    configure_logging(container.settings, log_file="logs/whale_alerts_worker.log")
    logger.info("Starting %s whale-alerts worker", container.settings.app_name)

    if not container.settings.enable_scheduler:
        # Same kill switch backend/worker.py's and scheduler_worker.py's
        # own processes check -- all three independently, so it still
        # disables all background work when set.
        logger.warning(
            "enable_scheduler is False -- whale-alerts worker has nothing to start, exiting"
        )
        return

    relay_provider = _build_relay_provider(
        container.settings.whale_alerts_relay_host, container.settings.whale_alerts_relay_port
    )
    logger.info(
        "Whale-alerts worker tracks %d symbols, ignoring the rest: %s",
        len(ACTIVE_UNDERLYINGS),
        ", ".join(underlying.symbol for underlying in ACTIVE_UNDERLYINGS),
    )
    await relay_provider.start()
    # Swaps only market_data_provider -- everything else (whale_alerts_
    # engine, its own dedicated Postgres pool, settings) comes from this
    # process's own real build_container() call, unchanged.
    relay_container = dataclasses.replace(container, market_data_provider=relay_provider)
    whale_alerts_stream = WhaleAlertsStreamManager(relay_container)
    whale_alerts_stream.start()
    # StreamStateExporter (see its own docstring/module comment) reads
    # container.whale_alerts_engine.symbol_flow() -- the accumulated
    # net-call/net-put premium process_trade() built up -- and persists
    # it for scheduler_worker.py to read back. That engine only ever
    # accumulates anything in THIS process now (backend/worker.py's own
    # copy sits idle since its own WhaleAlertsStreamManager moved here,
    # 2026-09-24), so this export has to run here too, not just in
    # worker.py. relay_container, not container: reuses the exact same
    # class unmodified -- its cumulative_volumes() export half reads
    # market_data_provider.cumulative_volumes(), which RelayDataProvider
    # always returns {} for (matches IDataProvider's own "stream never
    # started" contract), so that half is already a correct no-op here
    # without needing two different exporter classes.
    stream_state_exporter = StreamStateExporter(relay_container)
    stream_state_exporter.start()
    logger.info(
        "Whale-alerts worker running, connecting to relay at %s:%s",
        container.settings.whale_alerts_relay_host,
        container.settings.whale_alerts_relay_port,
    )

    try:
        # Runs forever -- Ctrl+C (KeyboardInterrupt) or the process being
        # killed is how this process is meant to stop, same as any other
        # long-lived server process.
        await asyncio.Event().wait()
    finally:
        logger.info("Stopping %s whale-alerts worker", container.settings.app_name)
        await whale_alerts_stream.stop()
        await stream_state_exporter.stop()
        await relay_provider.stop()
        # See this module's own docstring -- never started, but stop()
        # still closes its httpx.Client connection pool.
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
