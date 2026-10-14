"""Real-time chart price push over WebSocket.

Bridges backend/core/price_notifications.py's in-process
PriceNotificationHub (fed by the Worker's Postgres NOTIFYs) to whichever
browsers are watching a symbol's chart -- additive to the existing 30s
poll (GET /market/{symbol}), never a replacement: a client that never
connects, or whose connection drops, keeps working exactly as it does
today, off that poll.
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.container import Container
from backend.domain.use_cases.futures_proxy import future_level_offset_async, proxy_symbol_for

logger = logging.getLogger(__name__)

router = APIRouter(tags=["market-stream"])

# ES/NQ have no ticks of their own (ThetaData has no futures): the socket listens to the index
# they are drawn from (SPX/NDX) and sends each tick shifted by the same opening-price offset the
# REST chart uses. The offset is constant once today's opening price is typed; it is re-read now
# and then so a correction of that price reaches an open chart, and quickly while still missing.
OFFSET_REFRESH_SECONDS = 30.0
OFFSET_MISSING_RETRY_SECONDS = 5.0


def _shift_tick(payload: str, future: str, offset: Decimal) -> str | None:
    """The index tick expressed in the future's points, same JSON shape; None for a malformed one."""
    try:
        tick = json.loads(payload)
        tick["price"] = str(Decimal(str(tick["price"])) + offset)
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return None
    tick["symbol"] = future
    return json.dumps(tick)


@router.websocket("/ws/market/{symbol}")
async def market_price_stream(websocket: WebSocket, symbol: str) -> None:
    container: Container = websocket.app.state.container
    await websocket.accept()
    requested = symbol.upper()
    proxy = proxy_symbol_for(requested)
    queue = container.price_notification_hub.subscribe(proxy or symbol)
    offset: Decimal | None = None
    next_offset_read = 0.0
    try:
        while True:
            # subscribe()'s queue only ever receives ticks already
            # confirmed for this symbol (PriceNotificationHub.publish
            # filters by symbol before this loop ever sees a payload) --
            # forwarded to the client as-is, same JSON shape
            # AsyncPostgreSQLStorage.save_market_price wrote into the
            # NOTIFY payload (symbol/price/as_of), no reshaping needed.
            payload = await queue.get()
            if proxy is None:
                await websocket.send_text(payload)
                continue
            loop_time = asyncio.get_running_loop().time()
            if loop_time >= next_offset_read:
                offset = await future_level_offset_async(
                    container.async_market_storage, requested, proxy
                )
                next_offset_read = loop_time + (
                    OFFSET_REFRESH_SECONDS if offset is not None else OFFSET_MISSING_RETRY_SECONDS
                )
            if offset is None:
                # Today's opening price is not typed yet: nothing honest to send for the future.
                continue
            shifted = _shift_tick(payload, requested, offset)
            if shifted is not None:
                await websocket.send_text(shifted)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    finally:
        container.price_notification_hub.unsubscribe(proxy or symbol, queue)
