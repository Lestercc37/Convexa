"""Local-process relay for whale-alerts trade/quote events.

Why this exists: backend/worker.py owns the ONE real ThetaData WebSocket
connection (see ThetaStreamHub's own docstring -- Theta Terminal allows
exactly one) and must stay maximally responsive to it. Whale-alerts'
own per-trade classification (Lee-Ready side classification, bucketing,
threshold comparisons -- see WhaleAlertsEngine.process_trade) is real
CPU-bound work. Running it in the SAME process as the WebSocket read
loop -- even offloaded to a dedicated thread pool, as it was before this
module existed -- still shares that one process's GIL with the read
loop; heavy trade volume on the classification threads delays how often
the read loop's own coroutine actually gets to run. Confirmed live,
2026-09-24: 17 WebSocket reconnects and ~70,000 dropped trade messages
in under an hour, each disconnect preceded by ~10s of queue-saturation
CRITICAL logs -- the `websockets` library's own tiny incoming-frame
buffer (see WS_MAX_QUEUE in adapters/providers/thetadata/provider.py)
filling because the read loop couldn't drain it fast enough, itself
caused by GIL contention from the classification threads. Raising that
buffer (WS_MAX_QUEUE) is a real mitigation but not a fix for the
contention itself -- only a separate OS process (its own GIL) does that.

The split:
- `WhaleAlertsRelayServer` runs inside worker.py. It never classifies
  anything -- `WhaleAlertsRelayPublisher` (same module) feeds it raw
  FlowEvent/QuoteEvent objects straight off the real ThetaDataProvider's
  own stream_trades()/stream_quotes(), and it forwards each one,
  serialized, to whatever whale-alerts worker process is connected. This
  is cheap (JSON-encode + enqueue), so it runs directly on worker.py's
  own event loop -- no thread offload, nothing that competes for the
  GIL the read loop needs.
- `RelayDataProvider` runs inside backend/whale_alerts_worker.py, a
  separate OS process with its own GIL. It connects to the server above
  and re-exposes the same events through stream_trades()/stream_quotes()
  -- the exact shape IDataProvider already defines -- so
  WhaleAlertsStreamManager and StreamWhaleAlertsUseCase, the
  already-proven classification pipeline, run completely unchanged,
  just fed from here instead of a real ThetaData connection. Every other
  IDataProvider method raises NotImplementedError: this adapter only
  ever needs to satisfy the two streaming methods that pipeline calls.

Wire format: newline-delimited JSON, one small object per trade or
quote. Never crosses the network -- host/port (see Settings) default to
127.0.0.1 -- and never carries anything more sensitive than what was
already public market data on the wire from ThetaData itself.

Backpressure, both directions: same bounded-queue-plus-drop pattern
already used throughout this codebase (ThetaStreamHub's own per-symbol
queues) -- a slow/disconnected peer degrades whale-alerts coverage,
never blocks the caller. The messages this drops were always going to
end up in the exact same CRITICAL log this codebase already watches for
that condition, just relocated here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal

from backend.domain.entities import (
    DailyBar,
    FlowEvent,
    FlowEventType,
    MarketHoliday,
    MarketSnapshot,
    OptionChain,
    QuoteEvent,
    Side,
    UnderlyingTradeEvent,
)
from backend.domain.ports import IDataProvider
from backend.domain.underlyings import ACTIVE_UNDERLYINGS

logger = logging.getLogger(__name__)

# Same exponential-backoff shape/values already established for every
# other reconnect supervisor in this codebase (ThetaStreamHub, Whale
# Alerts' own trade-stream consumer, the Postgres LISTEN reconnect) --
# deliberately the same numbers, not a new convention here either.
RECONNECT_BASE_DELAY_SECONDS = 2
RECONNECT_MAX_DELAY_SECONDS = 60

# Per connected client (server side) and per symbol (client side) --
# generous, since what flows through here is now cheap-to-produce
# serialized JSON, not classification work: this is not the throughput
# ceiling, it's a sanity bound so a genuinely dead/hung peer degrades
# (drop + CRITICAL log, same pattern as every other queue in this
# codebase) instead of growing this process's memory without limit.
RELAY_QUEUE_MAXSIZE = 20000


def _encode_trade(event: FlowEvent) -> bytes:
    payload = {
        "k": "t",
        "symbol": event.symbol,
        "occ_symbol": event.occ_symbol,
        "as_of": event.as_of.isoformat(),
        "event_type": event.event_type.value,
        "premium": str(event.premium),
        "size": event.size,
        "aggressor_side": event.aggressor_side.value,
    }
    return (json.dumps(payload) + "\n").encode("utf-8")


def _decode_trade(payload: dict[str, object]) -> FlowEvent:
    return FlowEvent(
        symbol=str(payload["symbol"]),
        occ_symbol=str(payload["occ_symbol"]),
        as_of=datetime.fromisoformat(str(payload["as_of"])),
        event_type=FlowEventType(payload["event_type"]),
        premium=Decimal(str(payload["premium"])),
        size=int(payload["size"]),  # type: ignore[call-overload]
        aggressor_side=Side(payload["aggressor_side"]),
    )


def _encode_quote(event: QuoteEvent) -> bytes:
    payload = {
        "k": "q",
        "symbol": event.symbol,
        "occ_symbol": event.occ_symbol,
        "as_of": event.as_of.isoformat(),
        "bid": str(event.bid),
        "ask": str(event.ask),
    }
    return (json.dumps(payload) + "\n").encode("utf-8")


def _decode_quote(payload: dict[str, object]) -> QuoteEvent:
    return QuoteEvent(
        symbol=str(payload["symbol"]),
        occ_symbol=str(payload["occ_symbol"]),
        as_of=datetime.fromisoformat(str(payload["as_of"])),
        bid=Decimal(str(payload["bid"])),
        ask=Decimal(str(payload["ask"])),
    )


class _ConnectedClient:
    """One relay-server-side connection: an outbound queue plus the task
    that drains it onto the socket. A slow client's own queue fills and
    starts dropping (see `publish`) long before this could ever block
    the publisher that feeds every connected client."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=RELAY_QUEUE_MAXSIZE)
        self.send_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        try:
            while True:
                data = await self.queue.get()
                self.writer.write(data)
                # Drain the whole backlog queued up so far before
                # awaiting backpressure again, not once per message --
                # confirmed live, 2026-09-24: awaiting writer.drain()
                # after every single small write (trades AND quotes,
                # every active contract across 15 symbols, through one
                # queue+writer pair) couldn't keep up with real quote-
                # update volume even though nothing downstream was
                # actually falling behind -- 2M+ drops in under 20
                # minutes purely from drain-per-message overhead. Writing
                # everything currently queued before draining once lets
                # the OS-level socket buffer absorb a burst far more
                # efficiently than one drain() round-trip per message.
                while not self.queue.empty():
                    self.writer.write(self.queue.get_nowait())
                await self.writer.drain()
        except (ConnectionError, OSError):
            # The client disconnected -- WhaleAlertsRelayServer's own
            # accept-loop notices independently (reader.read() returning
            # empty) and removes this client; nothing more to do here.
            pass

    def publish(self, data: bytes) -> None:
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.critical(
                "Whale alerts relay: outbound queue full for a connected client "
                "(maxsize=%s) -- dropping one message. A whale-alerts worker "
                "process is connected but falling behind.",
                RELAY_QUEUE_MAXSIZE,
            )

    async def close(self) -> None:
        self.send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.send_task
        self.writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await self.writer.wait_closed()


class WhaleAlertsRelayServer:
    """Runs inside worker.py. Accepts connections from
    backend/whale_alerts_worker.py and broadcasts every published trade/
    quote event to all of them (in practice, exactly one). Publishing
    with no client connected is a silent no-op -- the whale-alerts
    worker process being down degrades whale-alerts coverage, never
    market data delivery, which is the entire point of this split."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._server: asyncio.base_events.Server | None = None
        self._clients: set[_ConnectedClient] = set()

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        # Real bound port, not necessarily self._port -- port=0 (used by
        # tests to avoid picking a real, possibly-in-use port) asks the OS
        # for any free one.
        self._port = self._server.sockets[0].getsockname()[1]
        logger.info(
            "Whale alerts relay server listening on %s:%s", self._host, self._port
        )

    @property
    def port(self) -> int:
        return self._port

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
        client = _ConnectedClient(writer)
        self._clients.add(client)
        logger.info("Whale alerts worker process connected to the relay")
        try:
            # This connection is one-directional (server -> client) --
            # nothing the client sends is ever meaningful, so the only
            # thing worth reading for is EOF (an empty read), the signal
            # this connection just closed.
            await reader.read()
        except (ConnectionError, OSError):
            pass
        finally:
            self._clients.discard(client)
            await client.close()
            logger.warning("Whale alerts worker process disconnected from the relay")

    def publish_trade(self, event: FlowEvent) -> None:
        if not self._clients:
            return
        data = _encode_trade(event)
        for client in self._clients:
            client.publish(data)

    def publish_quote(self, event: QuoteEvent) -> None:
        if not self._clients:
            return
        data = _encode_quote(event)
        for client in self._clients:
            client.publish(data)


class WhaleAlertsRelayPublisher:
    """Runs inside worker.py, alongside WhaleAlertsRelayServer. Forwards
    every active symbol's trade/quote events from the real
    ThetaDataProvider straight to the relay server -- deliberately doing
    nothing else (no classification, no Decimal bucketing math, no DB
    access) so this never becomes the next thing competing for the GIL
    the WebSocket read loop needs. Same per-symbol-task-plus-backoff
    lifecycle as WhaleAlertsStreamManager, which this replaces in
    worker.py (that class still exists, reused unchanged inside
    backend/whale_alerts_worker.py -- see its own docstring)."""

    def __init__(self, provider: IDataProvider, relay: WhaleAlertsRelayServer) -> None:
        self._provider = provider
        self._relay = relay
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._run_symbol(underlying.symbol))
            for underlying in ACTIVE_UNDERLYINGS
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def _run_symbol(self, symbol: str) -> None:
        delay = RECONNECT_BASE_DELAY_SECONDS
        while True:
            try:
                await asyncio.gather(
                    self._relay_trades(symbol),
                    self._relay_quotes(symbol),
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Whale alerts relay publisher failed for %s, restarting in %ss",
                    symbol,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)

    async def _relay_trades(self, symbol: str) -> None:
        async for event in self._provider.stream_trades(symbol):
            self._relay.publish_trade(event)

    async def _relay_quotes(self, symbol: str) -> None:
        async for event in self._provider.stream_quotes(symbol):
            self._relay.publish_quote(event)


class RelayDataProvider:
    """Runs inside backend/whale_alerts_worker.py. An IDataProvider-
    shaped adapter backed by WhaleAlertsRelayServer's local TCP
    connection, not a real ThetaData connection -- see this module's own
    docstring. Only stream_trades()/stream_quotes() do real work; every
    other IDataProvider method raises NotImplementedError, a loud
    failure if this adapter is ever wired somewhere that needs more than
    streaming, rather than a silent no-op standing in for real data."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._trade_queues: dict[str, asyncio.Queue[FlowEvent]] = {}
        self._quote_queues: dict[str, asyncio.Queue[QuoteEvent]] = {}
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        delay = RECONNECT_BASE_DELAY_SECONDS
        while True:
            try:
                await self._connect_and_relay()
                delay = RECONNECT_BASE_DELAY_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Whale alerts relay client lost connection to worker.py, "
                    "retrying in %ss",
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)

    async def _connect_and_relay(self) -> None:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        logger.info("Connected to the whale alerts relay at %s:%s", self._host, self._port)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    raise ConnectionError("Whale alerts relay server closed the connection")
                self._dispatch(json.loads(line))
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    def _dispatch(self, payload: dict[str, object]) -> None:
        if payload["k"] == "t":
            trade = _decode_trade(payload)
            queue = self._trade_queues.setdefault(
                trade.symbol, asyncio.Queue(maxsize=RELAY_QUEUE_MAXSIZE)
            )
            self._put(queue, trade, "trade", trade.symbol)
        else:
            quote = _decode_quote(payload)
            queue = self._quote_queues.setdefault(
                quote.symbol, asyncio.Queue(maxsize=RELAY_QUEUE_MAXSIZE)
            )
            self._put(queue, quote, "quote", quote.symbol)

    @staticmethod
    def _put(queue: asyncio.Queue[object], item: object, kind: str, symbol: str) -> None:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            logger.critical(
                "Whale alerts relay client: %s queue for %s is full (maxsize=%s) "
                "-- dropping one message. This process's own classification is "
                "falling behind.",
                kind,
                symbol,
                RELAY_QUEUE_MAXSIZE,
            )

    async def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]:
        queue = self._trade_queues.setdefault(
            underlying.upper(), asyncio.Queue(maxsize=RELAY_QUEUE_MAXSIZE)
        )
        while True:
            yield await queue.get()

    async def stream_quotes(self, underlying: str) -> AsyncIterator[QuoteEvent]:
        queue = self._quote_queues.setdefault(
            underlying.upper(), asyncio.Queue(maxsize=RELAY_QUEUE_MAXSIZE)
        )
        while True:
            yield await queue.get()

    # Everything below: this adapter is only ever wired into
    # WhaleAlertsStreamManager/StreamWhaleAlertsUseCase (see this
    # module's own docstring), which never calls any of these -- loud
    # failure on purpose if that ever changes.
    def get_option_chain(self, underlying: str, expiration: date | None = None) -> OptionChain:
        raise NotImplementedError("RelayDataProvider only supports streaming trades/quotes")

    def get_underlying_snapshot(self, underlying: str) -> MarketSnapshot:
        raise NotImplementedError("RelayDataProvider only supports streaming trades/quotes")

    def get_daily_bars(self, underlying: str, days: int = 20) -> list[DailyBar]:
        raise NotImplementedError("RelayDataProvider only supports streaming trades/quotes")

    def get_market_holidays(self, year: int) -> list[MarketHoliday]:
        raise NotImplementedError("RelayDataProvider only supports streaming trades/quotes")

    async def stream_underlying_trades(self, underlying: str) -> AsyncIterator[UnderlyingTradeEvent]:
        raise NotImplementedError("RelayDataProvider only supports streaming trades/quotes")
        yield  # pragma: no cover -- makes this a generator function, never reached

    def cumulative_volumes(self) -> dict[str, int]:
        # Matches IDataProvider's own documented contract for "a provider
        # instance whose stream was never started" -- this adapter never
        # tracks cumulative volume itself (StreamStateExporter's export,
        # backfilled from Postgres, is worker.py's job, not this
        # process's), so an empty dict here is correct, not a stub.
        return {}
