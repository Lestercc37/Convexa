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

v2 (2026-10-02), one-way only -- see docs/stream-processor-split-
postmortem-2026-10-02.md for the full postmortem on why v1 (PR #226)
collapsed in ~2.3 minutes under real volume. v1 had the processor send a
classified result back to worker.py for every raw frame -- structurally
TWO full-volume flows (raw frames out, classified results back) sharing
the same real ~20,000 msgs/sec rate, each needing its own queue, doubling
the total IPC/serialization burden regardless of whether either side
technically "waited" for anything. v2 removes the return flow entirely:
the stream processor process reaches every real destination directly
(Postgres for price/volume, its own WhaleAlertsRelayServer instance for
Whale Alerts -- see backend/stream_processor_worker.py's own docstring)
instead of reporting back to worker.py. This module now only ever moves
data in ONE direction, worker.py -> the processor, and worker.py never
reads anything from that connection at all.

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
  _handle_underlying_trade call when running in-process), and publishes
  each result directly to wherever it's actually consumed. Nothing is
  ever sent back over this connection.

Graceful degradation, same principle as WhaleAlertsRelayServer:
- No stream-processor client connected: StreamProcessorRelayServer.
  publish_raw() returns False, and ThetaStreamHub._process_messages()
  falls back to calling _handle_quote/_handle_option_trade/
  _handle_underlying_trade itself, in-process -- today's current,
  known-working (if GIL-contended) behavior. Never silently drops
  market data just because the new process isn't up yet. In this
  fallback mode, worker.py's own (unchanged) UnderlyingPriceStreamManager/
  WhaleAlertsRelayServer/StreamStateExporter keep working exactly as
  before this split existed, reading from ThetaStreamHub's own
  subscriber queues -- see worker.py's own comment on this tradeoff
  (fallback mode loses nothing for market data; Whale Alerts and
  cumulative-volume export pause only while the stream processor
  process itself is down, the same acceptable-degradation story this
  codebase already has for whale_alerts_worker.py's own downtime).
- Processor connected but falling behind reading raw frames: same
  bounded-queue-plus-drop pattern as every other queue in this codebase
  (RELAY_QUEUE_MAXSIZE, CRITICAL log, no block).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

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
    but only one is ever started). One-way only (server -> client) --
    nothing the client ever sends back is read for its content, same
    "only watch for EOF" shape as WhaleAlertsRelayServer's own
    _handle_client."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
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
            # One-way (server -> client): nothing the client sends is
            # ever meaningful, so the only thing worth reading for is
            # EOF (an empty read), the signal this connection just
            # closed. Same shape as WhaleAlertsRelayServer._handle_client.
            await reader.read()
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
    StreamProcessorRelayServer and calls `on_raw_frame` for every raw
    frame as it arrives. One-way only -- there is no method here to send
    anything back to worker.py; see this module's own docstring for why
    v2 (2026-10-02) deliberately removed that direction entirely rather
    than just making it "fire-and-forget" in name. Reconnects with the
    same exponential-backoff shape as every other reconnect supervisor in
    this codebase."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    async def run(self, on_raw_frame: Callable[[str], None]) -> None:
        """Connects and stays connected for this process's whole
        lifetime, reconnecting with backoff on any failure. `on_raw_frame`
        is called for every raw WS frame the server relays."""
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
        try:
            while True:
                line = await reader.readline()
                if not line:
                    raise ConnectionError("Stream processor relay server closed the connection")
                on_raw_frame(line.decode("utf-8").rstrip("\n"))
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()
