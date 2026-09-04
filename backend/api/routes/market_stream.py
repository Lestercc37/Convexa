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
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.container import Container

logger = logging.getLogger(__name__)

router = APIRouter(tags=["market-stream"])


@router.websocket("/ws/market/{symbol}")
async def market_price_stream(websocket: WebSocket, symbol: str) -> None:
    container: Container = websocket.app.state.container
    await websocket.accept()
    queue = container.price_notification_hub.subscribe(symbol)
    try:
        while True:
            # subscribe()'s queue only ever receives ticks already
            # confirmed for this symbol (PriceNotificationHub.publish
            # filters by symbol before this loop ever sees a payload) --
            # forwarded to the client as-is, same JSON shape
            # AsyncPostgreSQLStorage.save_market_price wrote into the
            # NOTIFY payload (symbol/price/as_of), no reshaping needed.
            payload = await queue.get()
            await websocket.send_text(payload)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    finally:
        container.price_notification_hub.unsubscribe(symbol, queue)
