from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Query, Request

from backend.api.schemas import (
    FlowResponse,
    GammaHistoryResponse,
    GammaResponse,
    OptionChainResponse,
    UnderlyingsResponse,
)
from backend.api.serializers import (
    chain_response,
    flow_response,
    gamma_history_response,
    gamma_response,
    underlyings_response,
)
from backend.core.container import Container
from backend.domain.use_cases import (
    get_flow,
    get_gamma_exposure,
    get_gamma_history,
    get_option_chain,
)

router = APIRouter(tags=["options"])


@router.get("/underlyings", response_model=UnderlyingsResponse)
def list_underlyings(request: Request) -> UnderlyingsResponse:
    container: Container = request.app.state.container
    return UnderlyingsResponse.model_validate(
        underlyings_response(container.storage.list_underlyings())
    )


@router.get("/chain/{symbol}", response_model=OptionChainResponse)
def get_chain(
    symbol: str,
    request: Request,
    expiration: date | None = None,
) -> OptionChainResponse:
    container: Container = request.app.state.container
    chain = get_option_chain(
        container.storage,
        container.market_data_provider,
        symbol,
        expiration,
    )
    return OptionChainResponse.model_validate(chain_response(chain))


@router.get("/gamma/{symbol}", response_model=GammaResponse)
def get_gamma(symbol: str, request: Request) -> GammaResponse:
    container: Container = request.app.state.container
    gamma = get_gamma_exposure(container.storage, symbol)
    return GammaResponse.model_validate(gamma_response(gamma))


@router.get("/gamma/{symbol}/history", response_model=GammaHistoryResponse)
def gamma_history(
    symbol: str,
    request: Request,
    start: datetime = Query(default=datetime.min.replace(tzinfo=timezone.utc)),
    end: datetime = Query(default=datetime.max.replace(tzinfo=timezone.utc)),
) -> GammaHistoryResponse:
    container: Container = request.app.state.container
    items = get_gamma_history(container.storage, symbol, start, end)
    return GammaHistoryResponse.model_validate(gamma_history_response(symbol, items))


@router.get("/flow/{symbol}", response_model=FlowResponse)
def flow(
    symbol: str,
    request: Request,
    since: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> FlowResponse:
    container: Container = request.app.state.container
    events = get_flow(container.storage, symbol, since, limit)
    return FlowResponse.model_validate(flow_response(symbol, events))
