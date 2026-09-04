from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Query, Request

from backend.api.schemas import (
    FlowResponse,
    GammaAggregateResponse,
    GammaFlipResponse,
    GammaHistoryResponse,
    GammaResponse,
    OptionChainResponse,
    UnderlyingsResponse,
)
from backend.api.serializers import (
    chain_response,
    flow_response,
    gamma_aggregate_response,
    gamma_flip_response,
    gamma_history_response,
    gamma_response,
    underlyings_response,
)
from backend.core.container import Container
from backend.domain.entities import GammaFlip
from backend.domain.use_cases import (
    calculate_derived_metrics_async,
    get_flow,
    get_gamma_exposure,
    get_gamma_exposure_async,
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
async def get_gamma(symbol: str, request: Request) -> GammaResponse:
    # async def, not def: dispatched on the event loop instead of
    # Starlette's shared threadpool, which the scheduler's own 15
    # concurrent asyncio.to_thread symbol refreshes (each with 3
    # sequential blocking ThetaData calls) can otherwise monopolize for
    # seconds -- confirmed live, 2026-09: this pure-storage-read route
    # measured 3.5-23s end to end while a scheduler cycle was in flight,
    # even though it never itself calls the data provider. See
    # AsyncPostgreSQLStorage's own docstring.
    container: Container = request.app.state.container
    gamma = await get_gamma_exposure_async(container.async_market_storage, symbol)
    derived_metrics = await calculate_derived_metrics_async(container.async_market_storage, symbol)
    return GammaResponse.model_validate(gamma_response(gamma, derived_metrics))


@router.get("/gamma/{symbol}/profile", response_model=GammaAggregateResponse)
def gamma_profile(symbol: str, request: Request) -> GammaAggregateResponse:
    container: Container = request.app.state.container
    gamma = get_gamma_exposure(container.storage, symbol)
    return GammaAggregateResponse.model_validate(gamma_aggregate_response(gamma))


@router.get("/gamma/{symbol}/flip", response_model=GammaFlipResponse)
def gamma_flip(symbol: str, request: Request) -> GammaFlipResponse:
    """The one honest, correctly-nullable representation of gamma_flip --
    GammaFlipResponse/gamma_flip_response() already existed for this
    (flip_found + a nullable gamma_flip_price) but no route used them
    before this. Reconstructed from the persisted GammaAggregate rather
    than a separately-stored GammaFlip -- only gamma_flip_price and
    flip_found are knowable from what's persisted; the interpolation
    detail fields (lower/upper_strike, lower/upper_gamma,
    interpolation_ratio) are transient, computed fresh on every cycle by
    CalculateGammaFlipUseCase, and never persisted at that level of
    detail, so they're honestly None here too, not guessed.
    """
    container: Container = request.app.state.container
    gamma = get_gamma_exposure(container.storage, symbol)
    flip = GammaFlip(gamma_flip_price=gamma.gamma_flip, flip_found=gamma.gamma_flip is not None)
    return GammaFlipResponse.model_validate(gamma_flip_response(flip))


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
