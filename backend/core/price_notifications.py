"""Real-time price push: Postgres LISTEN/NOTIFY bridged to the API's
WebSocket clients.

The Worker's StreamUnderlyingPriceUseCase (via AsyncPostgreSQLStorage.
save_market_price) sends a NOTIFY on MARKET_PRICE_CHANNEL inside the
same transaction as every MarketPrice it persists -- Postgres only
delivers it after that transaction commits, so a listener never sees a
tick that didn't really land. This process (the API) is the one that
listens and forwards to whichever browsers are watching each symbol's
chart, over a plain FastAPI WebSocket (see api/routes/market_stream.py).

Deliberately NOT using the pooled AsyncEngine (container.session_factory)
for the listening connection -- that pool is built for the
acquire/use-briefly/release pattern every other async read/write in
this codebase already follows (see AsyncPostgreSQLStorage's own
docstring), and a LISTEN connection needs the opposite: one connection
held open indefinitely, with a callback registered on it, for as long
as the process runs. Pool recycling or a health-check ping on that
connection would silently drop the LISTEN state without raising
anywhere obvious. Uses a raw asyncpg connection instead (asyncpg is
already a direct dependency, not just SQLAlchemy's driver -- see
pyproject.toml), with its own reconnect loop, same exponential-backoff
shape as ThetaTradeStream's WebSocket reconnect in
adapters/providers/thetadata/provider.py, for the same reason: a
dropped connection here must not be a silent, permanent stop.
"""

from __future__ import annotations

import asyncio
import json
import logging

import asyncpg
from sqlalchemy.engine import make_url

from backend.adapters.storage.postgresql_async import MARKET_PRICE_CHANNEL

logger = logging.getLogger(__name__)

RECONNECT_BASE_DELAY_SECONDS = 2
RECONNECT_MAX_DELAY_SECONDS = 60


class PriceNotificationHub:
    """In-memory registry of WebSocket clients per symbol, and the
    fan-out that pushes a tick to all of them.

    Per-process only -- if the API ever runs as more than one replica,
    each would hold its own registry, and a client connected to
    replica B would never see a NOTIFY delivered to replica A's
    listener. Not a concern with today's single-API-process topology;
    flagged here so it isn't rediscovered as a surprise later.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = {}

    def subscribe(self, symbol: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.setdefault(symbol.upper(), set()).add(queue)
        return queue

    def unsubscribe(self, symbol: str, queue: asyncio.Queue[str]) -> None:
        subscribers = self._subscribers.get(symbol.upper())
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            del self._subscribers[symbol.upper()]

    def publish(self, symbol: str, raw_payload: str) -> None:
        for queue in self._subscribers.get(symbol.upper(), ()):
            # put_nowait, not put: a stalled WebSocket client (one that
            # stopped reading) must never block this fan-out for every
            # other symbol/client -- an unbounded queue per subscriber
            # is the same tradeoff already accepted for
            # InMemoryStorage._price_history (no eviction, by design).
            queue.put_nowait(raw_payload)


class PriceNotificationListener:
    """Owns the one raw asyncpg LISTEN connection for the process's
    lifetime, forwarding every notification on MARKET_PRICE_CHANNEL to
    `hub`."""

    def __init__(self, database_url: str, hub: PriceNotificationHub) -> None:
        # asyncpg.connect() wants a plain "postgresql://" DSN, not
        # SQLAlchemy's "postgresql+asyncpg://" -- same URL massaging
        # infrastructure/database/engine.py's create_sync_engine
        # already does for its own driver swap.
        self._dsn = make_url(database_url).set(drivername="postgresql").render_as_string(
            hide_password=False
        )
        self._hub = hub
        self._connection: asyncpg.Connection | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _run(self) -> None:
        delay = RECONNECT_BASE_DELAY_SECONDS
        while True:
            try:
                await self._connect_and_listen()
                # _connect_and_listen only returns if the connection
                # closes on its own (e.g. Postgres restart) -- treat
                # that the same as any other disconnect, not a clean
                # exit.
                delay = RECONNECT_BASE_DELAY_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Price notification listener disconnected, retrying in %ss", delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)

    async def _connect_and_listen(self) -> None:
        connection = await asyncpg.connect(self._dsn)
        self._connection = connection
        try:
            await connection.add_listener(MARKET_PRICE_CHANNEL, self._on_notify)
            logger.info("Listening on Postgres channel %s", MARKET_PRICE_CHANNEL)
            # add_listener delivers notifications via callback on
            # asyncpg's own reader task -- this coroutine just has to
            # stay alive (and detect the connection dying) for as long
            # as the connection is meant to keep listening.
            while not connection.is_closed():
                await asyncio.sleep(1)
        finally:
            if not connection.is_closed():
                await connection.close()
            if self._connection is connection:
                self._connection = None

    def _on_notify(
        self, connection: asyncpg.Connection, pid: int, channel: str, raw_payload: str
    ) -> None:
        del connection, pid, channel  # required by asyncpg's callback signature, unused
        try:
            payload = json.loads(raw_payload)
            symbol = payload["symbol"]
        except (json.JSONDecodeError, KeyError):
            logger.warning("Malformed price notification payload: %r", raw_payload)
            return
        self._hub.publish(symbol, raw_payload)
