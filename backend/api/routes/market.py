from __future__ import annotations

from fastapi import APIRouter, Request

from backend.api.schemas import MarketSnapshotResponse
from backend.api.serializers import market_response
from backend.core.container import Container
from backend.domain.use_cases import build_market_snapshot_async

router = APIRouter(tags=["market"])


@router.get(
    "/market/{symbol}",
    response_model=MarketSnapshotResponse,
    summary="Get market snapshot",
)
async def get_market_snapshot(symbol: str, request: Request) -> MarketSnapshotResponse:
    # async def, not def: dispatched on the event loop instead of
    # Starlette's shared threadpool, which the scheduler's own 15
    # concurrent asyncio.to_thread symbol refreshes (each with 3
    # sequential blocking ThetaData calls) can otherwise monopolize for
    # seconds -- confirmed live, 2026-09: this pure-storage-read route
    # measured 3.5-23s end to end while a scheduler cycle was in flight,
    # even though it never itself calls the data provider. See
    # AsyncPostgreSQLStorage's own docstring.
    container: Container = request.app.state.container
    snapshot = await build_market_snapshot_async(container.async_market_storage, symbol)
    return MarketSnapshotResponse.model_validate(market_response(snapshot))
