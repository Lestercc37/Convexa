"""Standalone entrypoint for QUOTE/TRADE parsing and classification --
the CPU-bound work that used to run inline in backend/worker.py's own
ThetaStreamHub._process_messages, as a separate asyncio.Task on the same
event loop (PR #219, 2026-10-01).

Split out 2026-10-02 (v1, PR #226): PR #219 proved that separating the
WS read loop (_consume) from parsing/dispatch (_process_messages) into
two asyncio Tasks stopped the read loop from being directly blocked by
processing -- confirmed live, zero SLOW CONSUMER disconnects of that
specific kind since. It did NOT, and architecturally could not, give the
two genuine parallelism: both tasks still run on the same event loop,
same OS thread, same process, cooperatively interleaved at await points
only. Confirmed live, 2026-10-02 (message-lag diagnostic logging, added
the same day): _process_messages' own per-message work measured
100-150ms on individual iterations under real market-open volume
(~17,000-23,000 msgs/sec) -- during that whole synchronous span the
event loop cannot run _consume()'s own pending recv() either, enough to
build a backlog of 2,000-3,000+ messages at that rate. Matches Theta
Terminal's own independent "SLOW CONSUMER: N packets dropped" log for
the same incidents (1,062,092 dropped packets, 2026-10-02 9:50-11:55am
ET) and Eduardo/ThetaData support's own read of it: the GIL, not the
two-task split, was the real ceiling. Only a separate OS process -- its
own interpreter, its own GIL -- removes it.

v2 (2026-10-02), this file: v1 sent every classified result back to
worker.py over the relay and collapsed in ~2.3 minutes under real volume
-- see docs/stream-processor-split-postmortem-2026-10-02.md for the full
postmortem. The return flow doubled total IPC traffic to roughly the
same real ~20,000 msgs/sec rate each way, regardless of whether either
side technically "waited" for a reply; that's what exhausted a
20,000-item queue in 2.29 seconds. v2 removes the return flow entirely
and replicates the one pattern in this codebase already proven at this
exact volume, one-way: whale_alerts_relay.py (PR #179). This process now
reaches every real destination for a classified event DIRECTLY instead
of reporting back to worker.py:

- QUOTE/option TRADE -> WhaleAlertsRelayServer.publish_quote/publish_trade,
  a NEW instance of the exact same class worker.py used to own (moved,
  not duplicated -- see that class's own docstring in
  backend/core/whale_alerts_relay.py, completely unchanged). backend/
  whale_alerts_worker.py doesn't care which process answers on
  whale_alerts_relay_host:port, so this needed zero changes there.
- option TRADE size -> this process's own _cumulative_volume dict,
  exported to Postgres every EXPORT_INTERVAL_SECONDS (same cadence and
  same storage.save_cumulative_volumes call StreamStateExporter already
  uses, just owned here now since this is where the volume is actually
  tracked when a processor is connected).
- underlying TRADE -> StreamUnderlyingPriceUseCase.persist_if_due, the
  exact same market-hours-gated, per-symbol-debounced logic
  UnderlyingPriceStreamManager already uses in worker.py -- writes
  through AsyncPostgreSQLStorage.save_market_price, which NOTIFYs
  MARKET_PRICE_CHANNEL inside the same transaction (see
  core/price_notifications.py), so the chart's live WebSocket push picks
  it up exactly as it does today, regardless of which process wrote it.

This process never opens a connection to ThetaData itself -- Theta
Terminal allows exactly one WebSocket connection account-wide (see
ThetaStreamHub's own docstring), already held by backend/worker.py.
Instead it connects to that process's local-only relay
(StreamProcessorRelayServer, backend/core/stream_processor_relay.py)
over a plain TCP socket on this same machine, one-way: receives raw WS
frames, parses/classifies them with the exact same pure logic
ThetaStreamHub._handle_quote/_handle_option_trade/
_handle_underlying_trade call when running in-process (see
backend/adapters/providers/thetadata/stream_parsing.py -- literally the
same functions, not a reimplementation). Nothing is ever sent back over
that connection.

Deliberately stateless beyond ACTIVE_UNDERLYINGS (a static, shared
constant both processes import independently -- see
parse_underlying_trade_message's own `symbols` parameter) and its own
_cumulative_volume accumulator: no storage engine beyond what
build_container() already wires for the whale-alerts-relay/price-write
paths above.

If this process is down: worker.py's own StreamProcessorRelayServer.
publish_raw() just returns False (no client connected) and
_process_messages falls back to handling QUOTE/TRADE itself, in-process
-- today's known-working (if GIL-contended) behavior, market data never
silently dropped. In that fallback mode, worker.py's own (unchanged)
UnderlyingPriceStreamManager/WhaleAlertsRelayServer/StreamStateExporter
keep working exactly as before this whole split existed, reading from
ThetaStreamHub's own subscriber queues -- so Whale Alerts and the
tick-level price boost pause only while THIS process specifically is
down, the same acceptable-degradation story this codebase already
accepts for whale_alerts_worker.py's own downtime (see that module's
own docstring), now with one more link in the same chain. Core market
data (the REST scheduler's 30s cadence) is never affected either way --
the one invariant none of this is allowed to touch.

Run with:
    python -m backend.stream_processor_worker

Also run backend/worker.py (as its own separate process) -- without it,
this process has no relay server to connect to, and never receives a
single raw frame.
"""

from __future__ import annotations

import asyncio
import json
import logging

from backend.adapters.providers.thetadata.stream_parsing import (
    parse_option_trade_message,
    parse_quote_message,
    parse_underlying_trade_message,
)
from backend.core.container import build_container
from backend.core.logging import configure_logging
from backend.core.stream_processor_relay import StreamProcessorRelayClient
from backend.core.whale_alerts_relay import WhaleAlertsRelayServer
from backend.domain.entities import UnderlyingTradeEvent
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases.stream_underlying_price import StreamUnderlyingPriceUseCase

logger = logging.getLogger(__name__)

_SYMBOLS = {underlying.symbol: underlying.kind for underlying in ACTIVE_UNDERLYINGS}

# Same cadence as StreamStateExporter -- see that module's own comment
# for why 15s (frequent enough that the scheduler-only process's own
# ~60-75s cycle never reads anything more than one export interval
# stale, cheap enough not to matter at that cadence).
VOLUME_EXPORT_INTERVAL_SECONDS = 15

# Price writes in flight at once, across all symbols; well under the
# engine's pool (5 + 10 overflow) so the volume export and anything else
# always find a free connection.
PRICE_WRITE_CONCURRENCY = 4


class _ProcessorState:
    """Everything one raw frame's handling needs beyond the pure
    parse_*_message functions themselves -- bundled so
    _handle_raw_frame can stay a plain function (easy to unit test)
    instead of a bound method on a larger class."""

    def __init__(
        self,
        whale_alerts_relay: WhaleAlertsRelayServer,
        price_use_case: StreamUnderlyingPriceUseCase,
    ) -> None:
        self.whale_alerts_relay = whale_alerts_relay
        self.price_use_case = price_use_case
        self.cumulative_volume: dict[str, int] = {}
        self._pending_prices: dict[str, UnderlyingTradeEvent] = {}
        self._price_writers: dict[str, asyncio.Task[None]] = {}
        self._price_write_slots = asyncio.Semaphore(PRICE_WRITE_CONCURRENCY)

    def schedule_price_write(self, event: UnderlyingTradeEvent) -> None:
        """Latest-wins, one writer per symbol. Tick-level symbols are written
        on every tick (see TICK_LEVEL_SYMBOLS), and a task per tick exhausted
        the Postgres pool (5+10) within a minute of the 2026-10-05 open: the
        tasks piled up waiting for a connection, the processor stopped
        consuming and the stored price froze. A write only ever needs the
        newest tick, so while one is in flight newer ticks just replace the
        pending one."""
        self._pending_prices[event.symbol] = event
        writer = self._price_writers.get(event.symbol)
        if writer is None or writer.done():
            self._price_writers[event.symbol] = asyncio.create_task(self._drain_price_writes(event.symbol))

    async def _drain_price_writes(self, symbol: str) -> None:
        while True:
            event = self._pending_prices.pop(symbol, None)
            if event is None:
                return
            try:
                async with self._price_write_slots:
                    await self.price_use_case.persist_if_due(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stream processor worker: persisting the %s price failed", symbol)


def _handle_raw_frame(state: _ProcessorState, raw: str) -> None:
    """Parses one raw WS frame and publishes the result directly to
    wherever it's actually consumed -- see this module's own docstring.
    Synchronous and exception-isolated per frame: a single malformed
    message must never take down this process's whole relay connection,
    same principle as StreamProcessorRelayServer's own reader loop."""
    try:
        message = json.loads(raw)
        header = message.get("header", {})
        msg_type = header.get("type")
        if msg_type == "QUOTE":
            parsed = parse_quote_message(message)
            if parsed is not None:
                state.whale_alerts_relay.publish_quote(parsed.event)
        elif msg_type == "TRADE":
            security_type = message.get("contract", {}).get("security_type")
            if security_type == "OPTION":
                parsed = parse_option_trade_message(message)
                if parsed is not None:
                    state.cumulative_volume[parsed.occ_symbol] = (
                        state.cumulative_volume.get(parsed.occ_symbol, 0) + parsed.size
                    )
                    if parsed.event is not None:
                        state.whale_alerts_relay.publish_trade(parsed.event)
            elif security_type in ("STOCK", "INDEX"):
                parsed_underlying = parse_underlying_trade_message(message, _SYMBOLS)
                if parsed_underlying is not None:
                    # Fire-and-forget on purpose: persist_if_due does a
                    # real async Postgres read+write when due (most
                    # calls return instantly on the debounce check
                    # alone) -- awaiting it inline here would serialize
                    # raw-frame handling behind that I/O, exactly the
                    # kind of self-inflicted backpressure v1's postmortem
                    # flagged. asyncio.create_task keeps this frame's
                    # handling non-blocking regardless. Coalesced per
                    # symbol (ProcessorState.schedule_price_write) so a
                    # tick burst can't pile up unbounded writes.
                    state.schedule_price_write(parsed_underlying.event)
    except Exception:
        logger.exception("Stream processor worker: failed to parse one raw frame, dropping it")


async def _export_volume_periodically(container, state: _ProcessorState) -> None:
    """This process's own half of what StreamStateExporter already does
    for worker.py's in-process fallback path -- same cadence, same
    storage call, owned here because this is where volume is actually
    tracked whenever a processor is connected. See save_cumulative_
    volumes' own guard against an empty dict -- worker.py's own
    StreamStateExporter keeps running unconditionally too (harmless,
    its own _cumulative_volume just stays empty while this process is
    the one doing real classification, so it never has anything to
    export, let alone something that could overwrite this process's
    own, fresher data)."""
    while True:
        await asyncio.sleep(VOLUME_EXPORT_INTERVAL_SECONDS)
        try:
            if state.cumulative_volume:
                await asyncio.to_thread(
                    container.storage.save_cumulative_volumes, dict(state.cumulative_volume)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Stream processor worker: cumulative volume export failed, will retry "
                "next interval"
            )


async def run() -> None:
    container = build_container()
    configure_logging(container.settings, log_file="logs/stream_processor_worker.log")
    logger.info("Starting %s stream processor worker", container.settings.app_name)

    if not container.settings.enable_scheduler:
        # Same kill switch backend/worker.py's own process checks.
        logger.warning(
            "enable_scheduler is False -- stream processor worker has nothing to "
            "start, exiting"
        )
        return

    whale_alerts_relay = WhaleAlertsRelayServer(
        container.settings.whale_alerts_relay_host, container.settings.whale_alerts_relay_port
    )
    await whale_alerts_relay.start()
    price_use_case = StreamUnderlyingPriceUseCase(
        provider=container.market_data_provider,  # never streamed from here, see this
        # module's own docstring -- persist_if_due is the only method
        # this process ever calls on it.
        storage=container.async_market_storage,
    )
    state = _ProcessorState(whale_alerts_relay, price_use_case)
    export_task = asyncio.create_task(_export_volume_periodically(container, state))

    relay = StreamProcessorRelayClient(
        container.settings.stream_processor_relay_host,
        container.settings.stream_processor_relay_port,
    )
    logger.info(
        "Stream processor worker running, connecting to relay at %s:%s -- publishing "
        "Whale Alerts directly on %s:%s",
        container.settings.stream_processor_relay_host,
        container.settings.stream_processor_relay_port,
        container.settings.whale_alerts_relay_host,
        container.settings.whale_alerts_relay_port,
    )
    try:
        await relay.run(on_raw_frame=lambda raw: _handle_raw_frame(state, raw))
    finally:
        export_task.cancel()
        try:
            await export_task
        except asyncio.CancelledError:
            pass
        await whale_alerts_relay.stop()
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
