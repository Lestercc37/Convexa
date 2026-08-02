from __future__ import annotations

from fastapi import APIRouter, Request

from backend.api.schemas import MarketSnapshotResponse
from backend.api.serializers import market_response
from backend.core.container import Container
from backend.domain.use_cases import build_market_snapshot

router = APIRouter(tags=["market"])


@router.get(
    "/market/{symbol}",
    response_model=MarketSnapshotResponse,
    summary="Get market snapshot",
)
def get_market_snapshot(symbol: str, request: Request) -> MarketSnapshotResponse:
    container: Container = request.app.state.container
    snapshot = build_market_snapshot(container.storage, symbol)
    return MarketSnapshotResponse.model_validate(market_response(snapshot))
