"""Local-process relay splitting ThetaStreamHub's WS read loop from
QUOTE/TRADE parsing/classification -- the same GIL-contention problem,
and the same fix shape, as whale_alerts_relay.py (PR #179), applied one
level earlier in the pipeline.

Why this exists, beyond what PR #219 (the _consume()/_process_messages
task split) already did: _consume() and _process_messages() are two
asyncio.Task objects, but both still run on the SAME event loop, SAME
OS thread, SAME process -- cooperative interleaving, not real
parallelism. Confirmed live, 2026-10-02 (message-lag diagnostic logging,
added the same day): _process_messages' own per-message work (json.loads
+ OCC-symbol construction + Decimal math + per-queue dispatch) measured
100-150ms on individual iterations under real market-open volume
(~17,000-23,000 msgs/sec). During that whole synchronous span the event
loop cannot run ANY other coroutine, _consume()'s own pending recv()
included -- at that volume, 100-150ms of the single thread being
unavailable is enough to build a backlog of 2,000-3,000+ messages,
matching both this project's own queue-depth logs and (independently)
Theta Terminal's own "SLOW CONSUMER: N packets dropped" log on the exact
same incidents (1,062,092 dropped packets, 2026-10-02 9:50-11:55am ET).
Only a genuinely separate OS process -- its own interpreter, its own GIL
-- gives the read loop and this parsing work real concurrency.

The split:
- `StreamProcessorRelayServer` runs inside worker.py, right where
  _process_messages() used to do the heavy lifting itself. It still does
  the CHEAP work (one json.loads() per message to read msg_type/
  security_type for watchdog bookkeeping and routing -- confirmed
  negligible, not the 100-150ms cost) and then hands the raw frame
  (unmodified, no re-encoding) to whichever stream-processor worker
  process is connected, same cheap enqueue-and-return shape as the
  WhaleAlertsRelayServer/_message_queue split before it.
- `backend/stream_processor_worker.py`, a separate OS process with its
  own GIL, connects to the server above, does the actual parsing
  (backend/adapters/providers/thetadata/stream_parsing.py's pure
  parse_quote_message/parse_option_trade_message/
  parse_underlying_trade_message -- the exact same logic
  ThetaStreamHub._handle_quote/_handle_option_trade/
  _handle_underlying_trade call when running in-process), and sends the
  already-classified result back over the SAME connection.
- Back in worker.py, StreamProcessorRelayServer's own reader task takes
  that classified result and does ONLY the cheap tail end
  (ThetaStreamHub._handle_processed_quote/_trade/_underlying_trade --
  record message lag, update _cumulative_volume, dispatch to the exact
  same subscriber queues _handle_quote/etc. already dispatch to) --
  PriceNotificationHub, WhaleAlertsRelayPublisher, StreamStateExporter
  and the watchdog all keep reading from those same queues/state,
  completely unaware anything moved.

Deliberately duplex over ONE TCP connection, not two -- raw frames
flow server->client, classified events flow client->server, both
directions cheap JSON-per-line, matching whale_alerts_relay.py's own
wire-format convention (newline-delimited JSON) and localhost-only
scope (127.0.0.1, see Settings) for the same reason: this never crosses
a real network boundary and never carries anything ThetaData itself
didn't already put on the wire.

Graceful degradation, both ways, same principle as WhaleAlertsRelayServer:
- No stream-processor client connected: StreamProcessorRelayServer.
  publish_raw() returns False, and ThetaStreamHub._process_messages()
  falls back to calling _handle_quote/_handle_option_trade/
  _handle_underlying_trade itself, in-process -- today's current,
  known-working (if GIL-contended) behavior. Never silently drops
  market data just because the new process isn't up yet.
- Processor connected but falling behind: same bounded-queue-plus-drop
  pattern as every other queue in this codebase (RELAY_QUEUE_MAXSIZE,
  CRITICAL log, no block) in both directions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Same values as whale_alerts_relay.py -- deliberately not a new
# convention here either.
RECONNECT_BASE_DELAY_SECONDS = 2
RECONNECT_MAX_DELAY_SECONDS = 60

# Generous, matching whale_alerts_relay.py's own reasoning: this is not
# the throughput ceiling, it's a sanity bound so a genuinely dead/hung
# peer degrades (drop + CRITICAL log) instead of growing either
# process's memory without limit.
RELAY_QUEUE_MAXSIZE = 20000


class StreamProcessorRelayServer:
    """Runs inside worker.py. Accepts exactly one connection from
    backend/stream_processor_worker.py (in practice; a second connection
    is accepted and used the same way, nothing here assumes exclusivity,
    but only one is ever started). `on_event` is called for every
    classified result the processor sends back -- wired to
    ThetaStreamHub._handle_processed_event."""

    def __init__(self, host: str, port: int, on_event: Callable[[dict[str, Any]], None]) -> None:
        self._host = host
        self._port = port
        self._on_event = on_event
        self._server: asyncio.base_events.Server | None = None
        self._clients: set["_ConnectedProcessor"] = set()

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        # Real bound port, not necessarily self._port -- port=0 (tests)
        # asks the OS for any free one, same convention as
        # WhaleAlertsRelayServer.
        self._port = self._server.sockets[0].getsockname()[1]
        logger.info("Stream processor relay server listening on %s:%s", self._host, self._port)

    @property
    def port(self) -> int:
        return self._port

    @property
    def has_client(self) -> bool:
        return bool(self._clients)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for client in list(self._clients):
            await client.close()
        self._clients.clear()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        client = _ConnectedProcessor(writer)
        self._clients.add(client)
        logger.info("Stream processor worker connected to the relay")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    self._on_event(json.loads(line))
                except Exception:
                    logger.exception(
                        "Stream processor relay server: failed to handle one classified "
                        "event, dropping it and continuing"
                    )
        except (ConnectionError, OSError):
            pass
        finally:
            self._clients.discard(client)
            await client.close()
            logger.warning("Stream processor worker disconnected from the relay")

    def publish_raw(self, raw: str) -> bool:
        """Hands one raw WS frame to a connected processor. Returns
        False (no-op) when nothing is connected -- the caller
        (ThetaStreamHub._process_messages) must fall back to handling it
        in-process itself in that case, see this module's own docstring
        on graceful degradation."""
        if not self._clients:
            return False
        data = (raw + "\n").encode("utf-8") if not raw.endswith("\n") else raw.encode("utf-8")
        for client in self._clients:
            client.publish(data)
        return True


class _ConnectedProcessor:
    """One relay-server-side connection: an outbound queue plus the task
    that drains it onto the socket. Same batch-drain-before-single-
    drain() shape as whale_alerts_relay.py's _ConnectedClient -- awaiting
    writer.drain() after every single small write could not keep up with
    real quote-update volume there (confirmed live, 2026-09-24, 2M+
    drops in under 20 minutes from drain-per-message overhead alone);
    same risk here, same fix."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=RELAY_QUEUE_MAXSIZE)
        self.send_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        try:
            while True:
                data = await self.queue.get()
                self.writer.write(data)
                while not self.queue.empty():
                    self.writer.write(self.queue.get_nowait())
                await self.writer.drain()
        except (ConnectionError, OSError):
            pass

    def publish(self, data: bytes) -> None:
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.critical(
                "Stream processor relay: outbound (raw frame) queue full for the "
                "connected processor (maxsize=%s) -- dropping one message. The "
                "stream processor process is connected but falling behind.",
                RELAY_QUEUE_MAXSIZE,
            )

    async def close(self) -> None:
        self.send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.send_task
        self.writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await self.writer.wait_closed()


class StreamProcessorRelayClient:
    """Runs inside backend/stream_processor_worker.py. Connects to
    StreamProcessorRelayServer, yields raw frames as they arrive
    (`raw_frames()`), and sends classified results back (`publish_event`).
    Reconnects with the same exponential-backoff shape as every other
    reconnect supervisor in this codebase."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._outbound: asyncio.Queue[bytes] = asyncio.Queue(maxsize=RELAY_QUEUE_MAXSIZE)

    def publish_event(self, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            self._outbound.put_nowait(data)
        except asyncio.QueueFull:
            logger.critical(
                "Stream processor relay client: outbound (classified event) queue "
                "full (maxsize=%s) -- dropping one message. This process's own "
                "connection back to worker.py is falling behind.",
                RELAY_QUEUE_MAXSIZE,
            )

    async def run(self, on_raw_frame: Callable[[str], None]) -> None:
        """Connects and stays connected for this process's whole
        lifetime, reconnecting with backoff on any failure. `on_raw_frame`
        is called for every raw WS frame the server relays -- wired to
        the processor's own parse-and-republish loop."""
        delay = RECONNECT_BASE_DELAY_SECONDS
        while True:
            try:
                await self._connect_and_relay(on_raw_frame)
                delay = RECONNECT_BASE_DELAY_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Stream processor relay client lost connection to worker.py, "
                    "retrying in %ss",
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)

    async def _connect_and_relay(self, on_raw_frame: Callable[[str], None]) -> None:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        logger.info("Connected to the stream processor relay at %s:%s", self._host, self._port)
        send_task = asyncio.create_task(self._drain_outbound(writer))
        try:
            while True:
                line = await reader.readline()
                if not line:
                    raise ConnectionError("Stream processor relay server closed the connection")
                on_raw_frame(line.decode("utf-8").rstrip("\n"))
        finally:
            send_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await send_task
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def _drain_outbound(self, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await self._outbound.get()
                writer.write(data)
                while not self._outbound.empty():
                    writer.write(self._outbound.get_nowait())
                await writer.drain()
        except (ConnectionError, OSError):
            pass
