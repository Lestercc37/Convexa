from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from backend.api.schemas import MarketSnapshotResponse, PriceHistoryResponse, VwapHistoryResponse
from backend.api.serializers import market_response, price_history_response, vwap_history_response
from backend.core.container import Container
from backend.domain.use_cases import (
    build_market_snapshot_async,
    calculate_session_open,
    get_vwap_history_async,
)

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


@router.get(
    "/market/{symbol}/history",
    response_model=PriceHistoryResponse,
    summary="Get today's session price history",
)
async def get_price_history(symbol: str, request: Request) -> PriceHistoryResponse:
    """Every point `market_snapshots` holds for `symbol` since today's
    09:30 ET session open -- the same data `build_market_snapshot`
    already reads internally for anchored VWAP/ATR, just exposed
    directly this time. Lets the frontend seed the chart's candles with
    everything already formed today instead of starting from an empty
    chart on mount/symbol change (see dashboard.tsx's own comment on
    `pricePoints`).
    """
    container: Container = request.app.state.container
    now = datetime.now(timezone.utc)
    points = await container.async_market_storage.get_price_history(
        symbol, calculate_session_open(now), now
    )
    return PriceHistoryResponse.model_validate(price_history_response(symbol, points))


@router.get(
    "/market/{symbol}/vwap-history",
    response_model=VwapHistoryResponse,
    summary="Get today's session Anchored VWAP series",
)
async def get_vwap_history(symbol: str, request: Request) -> VwapHistoryResponse:
    """Every Anchored VWAP point computable so far today, one per
    session reading -- lets the frontend seed the VWAP line on
    mount/symbol-change the same way `/market/{symbol}/history` already
    seeds candles, instead of starting the line over from empty (see
    dashboard-spec.md's own note on this gap). `not_applicable=True`
    for pure indices (SPX/NDX/VIX) -- see AnchoredVwap's own docstring.
    """
    container: Container = request.app.state.container
    series, not_applicable = await get_vwap_history_async(container.async_market_storage, symbol)
    return VwapHistoryResponse.model_validate(
        vwap_history_response(symbol, series, not_applicable)
    )
