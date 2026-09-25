from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Query, Request

from backend.api.schemas import (
    ChainExpirationsResponse,
    FlowPressureResponse,
    FlowResponse,
    GammaAggregateResponse,
    GammaFlipResponse,
    GammaHistoryResponse,
    GammaResponse,
    OptionChainResponse,
    UnderlyingsResponse,
)
from backend.api.serializers import (
    chain_expirations_response,
    chain_response,
    flow_pressure_response,
    flow_response,
    gamma_aggregate_response,
    gamma_flip_response,
    gamma_history_response,
    gamma_response,
    underlyings_response,
)
from backend.core.container import Container
from backend.domain.entities import GammaFlip, GammaView
from backend.domain.use_cases import (
    calculate_derived_metrics_async,
    get_flow,
    get_gamma_exposure,
    get_gamma_exposure_async,
    get_gamma_history,
    get_option_chain_async,
    get_option_chain_expirations,
    get_symbol_flow_pressure_async,
)

router = APIRouter(tags=["options"])


@router.get("/underlyings", response_model=UnderlyingsResponse)
def list_underlyings(request: Request) -> UnderlyingsResponse:
    container: Container = request.app.state.container
    return UnderlyingsResponse.model_validate(
        underlyings_response(container.storage.list_underlyings())
    )


@router.get("/chain/{symbol}", response_model=OptionChainResponse)
async def get_chain(
    symbol: str,
    request: Request,
    expiration: date | None = None,
) -> OptionChainResponse:
    # async def, not def -- confirmed live, 2026-09-22: the plain `def`
    # version of this route hung 40+ seconds and produced real 500s
    # under real market-open load, starved by the same shared threadpool
    # the scheduler's own concurrent symbol refreshes use (same root
    # cause /gamma/{symbol} was already fixed for -- see that route's
    # own comment). get_option_chain_async reads the common case (a
    # fresh-enough stored snapshot) purely on the event loop, no thread
    # involved; only the rare stale-during-market-hours case falls
    # through to a worker thread (see that function's own docstring).
    container: Container = request.app.state.container
    chain = await get_option_chain_async(
        container.async_market_storage,
        container.storage,
        container.market_data_provider,
        symbol,
        expiration,
    )
    return OptionChainResponse.model_validate(chain_response(chain))


@router.get("/chain/{symbol}/expirations", response_model=ChainExpirationsResponse)
def get_chain_expirations(symbol: str, request: Request) -> ChainExpirationsResponse:
    # The option-chain-viewer/Volatility Smile UI's own expiration dropdown
    # only ever needs the distinct dates, not a single contract's worth of
    # greeks/OI/bid/ask -- confirmed live, 2026-09-22: fetching the full
    # unscoped chain just to read off `.expiration` cost a 2.3MB / ~8,000-
    # contract response for SPX alone (widened by the Gamma Flip fix, PR
    # #159), heavy enough to help starve /chain/{symbol}'s shared
    # threadpool. get_option_chain_expirations, not get_option_chain --
    # storage-only, never falls through to a live provider fetch (see its
    # own docstring): confirmed live the same day this route's OWN
    # get_option_chain call, gated on a 60s freshness window, could hang
    # 40+ seconds waiting on the same thread pool and ThetaData
    # concurrency semaphore the scheduler's cycle was saturating --
    # expiration dates don't need to be that fresh to be correct.
    container: Container = request.app.state.container
    chain = get_option_chain_expirations(container.storage, symbol)
    return ChainExpirationsResponse.model_validate(chain_expirations_response(chain))


@router.get("/gamma/{symbol}", response_model=GammaResponse)
async def get_gamma(
    symbol: str,
    request: Request,
    view: GammaView = Query(default="structural"),
) -> GammaResponse:
    # async def, not def: dispatched on the event loop instead of
    # Starlette's shared threadpool, which the scheduler's own 15
    # concurrent asyncio.to_thread symbol refreshes (each with 3
    # sequential blocking ThetaData calls) can otherwise monopolize for
    # seconds -- confirmed live, 2026-09: this pure-storage-read route
    # measured 3.5-23s end to end while a scheduler cycle was in flight,
    # even though it never itself calls the data provider. See
    # AsyncPostgreSQLStorage's own docstring.
    #
    # derived_metrics stays Structural-only regardless of `view`,
    # deliberately (2026-09-25, confirmed with the user): Dealer Impact
    # Score/Signal Alignment Score/Market Bias depend on historical
    # comparisons (DailyGammaReference) that only exist for the
    # Structural window -- see capture_daily_gamma_reference's own
    # Structural-only scoping in refresh_snapshot.py.
    container: Container = request.app.state.container
    gamma = await get_gamma_exposure_async(container.async_market_storage, symbol, view=view)
    derived_metrics = await calculate_derived_metrics_async(container.async_market_storage, symbol)
    return GammaResponse.model_validate(gamma_response(gamma, derived_metrics))


@router.get("/gamma/{symbol}/profile", response_model=GammaAggregateResponse)
def gamma_profile(
    symbol: str, request: Request, view: GammaView = Query(default="structural")
) -> GammaAggregateResponse:
    container: Container = request.app.state.container
    gamma = get_gamma_exposure(container.storage, symbol, view=view)
    return GammaAggregateResponse.model_validate(gamma_aggregate_response(gamma))


@router.get("/gamma/{symbol}/flip", response_model=GammaFlipResponse)
def gamma_flip(
    symbol: str, request: Request, view: GammaView = Query(default="structural")
) -> GammaFlipResponse:
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
    gamma = get_gamma_exposure(container.storage, symbol, view=view)
    flip = GammaFlip(gamma_flip_price=gamma.gamma_flip, flip_found=gamma.gamma_flip is not None)
    return GammaFlipResponse.model_validate(gamma_flip_response(flip))


@router.get("/gamma/{symbol}/history", response_model=GammaHistoryResponse)
def gamma_history(
    symbol: str,
    request: Request,
    start: datetime = Query(default=datetime.min.replace(tzinfo=timezone.utc)),
    end: datetime = Query(default=datetime.max.replace(tzinfo=timezone.utc)),
    view: GammaView = Query(default="structural"),
) -> GammaHistoryResponse:
    container: Container = request.app.state.container
    items = get_gamma_history(container.storage, symbol, start, end, view=view)
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


@router.get(
    "/flow/{symbol}/pressure",
    response_model=FlowPressureResponse,
    summary="Net client options flow pressure",
    description=(
        "Net CLIENT (aggressor) options premium flow, classified by "
        "Lee-Ready -- NOT a confirmed reading of dealer positioning. "
        "See the response's own methodology_note."
    ),
)
async def flow_pressure(symbol: str, request: Request) -> FlowPressureResponse:
    # async def, not def: same reasoning as /gamma and /market -- a pure
    # storage read (see AsyncPostgreSQLStorage's own docstring).
    container: Container = request.app.state.container
    flow = await get_symbol_flow_pressure_async(container.async_market_storage, symbol)
    return FlowPressureResponse.model_validate(flow_pressure_response(flow))
