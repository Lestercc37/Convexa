from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from backend.api.schemas import (
    ChainExpirationsResponse,
    FlowPressureResponse,
    FlowResponse,
    GammaAggregateResponse,
    GammaFlipResponse,
    GammaHeatmapResponse,
    GammaHistoryResponse,
    GammaResponse,
    MinuteFlowResponse,
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
    gamma_heatmap_response,
    gamma_history_response,
    gamma_response,
    minute_flow_response,
    underlyings_response,
)
from backend.core.container import Container
from backend.domain.entities import GammaFlip, GammaView
from backend.domain.use_cases.futures_proxy import proxy_symbol_for
from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.domain.use_cases.volume_corrections import apply_volume_corrections
from backend.domain.use_cases import (
    PRICE_PROXY_SYMBOL_BY_FUTURE,
    calculate_derived_metrics_async,
    get_flow,
    get_gamma_exposure,
    get_gamma_exposure_async,
    get_gamma_heatmap_async,
    get_gamma_history,
    get_option_chain_async,
    get_option_chain_expirations_async,
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
    # TOTALS only: the live counters stay continuous for everything that computes CHANGES of volume; this reply shows max(counter,
    # correction) so a contract that entered the stream band late is not short (migration 0044, backend/volume_correction_pass.py).
    corrections = await container.async_market_storage.get_volume_corrections(
        [contract.occ_symbol for contract in chain.contracts]
    )
    chain = apply_volume_corrections(chain, corrections)
    return OptionChainResponse.model_validate(chain_response(chain))


@router.get("/chain/{symbol}/expirations", response_model=ChainExpirationsResponse)
async def get_chain_expirations(symbol: str, request: Request) -> ChainExpirationsResponse:
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
    # async def + the async storage: the sync read of the latest snapshot takes 26-32 s for SPX/NDX when the market is closed (see
    # get_option_chain_expirations_async), which made the Volatility Smile dropdown fail off-hours.
    chain = await get_option_chain_expirations_async(container.async_market_storage, symbol)
    return ChainExpirationsResponse.model_validate(chain_expirations_response(chain))


@router.get("/gamma/{symbol}", response_model=GammaResponse)
async def get_gamma(
    symbol: str,
    request: Request,
    view: GammaView = Query(default="tactical"),
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
    symbol: str, request: Request, view: GammaView = Query(default="tactical")
) -> GammaAggregateResponse:
    container: Container = request.app.state.container
    gamma = get_gamma_exposure(container.storage, symbol, view=view)
    return GammaAggregateResponse.model_validate(gamma_aggregate_response(gamma))


@router.get("/gamma/{symbol}/flip", response_model=GammaFlipResponse)
def gamma_flip(
    symbol: str, request: Request, view: GammaView = Query(default="tactical")
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
    view: GammaView = Query(default="tactical"),
) -> GammaHistoryResponse:
    container: Container = request.app.state.container
    items = get_gamma_history(container.storage, symbol, start, end, view=view)
    return GammaHistoryResponse.model_validate(gamma_history_response(symbol, items))


HEATMAP_MAX_RANGE = timedelta(days=5)


@router.get(
    "/gamma/{symbol}/heatmap",
    response_model=GammaHeatmapResponse,
    summary="Strike x time matrix of a stored per-strike figure",
    description=(
        "Reads gamma_aggregate_items (written every scheduler cycle) and bins it in time; nothing is calculated here. "
        "Defaults to the last 24 hours, at most 5 days per request. ES/NQ are SPX/NDX shifted by today's opening price, "
        "so they only cover the current ET date."
    ),
)
async def gamma_heatmap(
    symbol: str,
    request: Request,
    view: GammaView = Query(default="tactical"),
    metric: Literal["gamma", "absolute_gamma", "delta", "vanna", "charm", "call_gamma", "put_gamma", "total_gamma"] = Query(default="gamma"),
    start: datetime | None = None,
    end: datetime | None = None,
    bucket_seconds: int = Query(default=120, ge=10, le=3600),
    source: Literal["cycle", "fast"] = Query(default="cycle"),
) -> GammaHeatmapResponse:
    # async def, not def: a plain storage read, same reasoning as /gamma and /market.
    container: Container = request.app.state.container
    is_future = symbol.upper() in PRICE_PROXY_SYMBOL_BY_FUTURE
    today_start = datetime.now(EASTERN_TIME).replace(hour=0, minute=0, second=0, microsecond=0)
    end = end or datetime.now(timezone.utc)
    start = start or (today_start if is_future else end - timedelta(days=1))
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=422, detail="start and end must include a timezone (e.g. ...Z)")
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    if end - start > HEATMAP_MAX_RANGE:
        raise HTTPException(status_code=422, detail="the heatmap range is limited to 5 days per request")
    if is_future:
        if start < today_start:
            raise HTTPException(status_code=422, detail=f"{symbol.upper()} heatmap covers the current ET date only")
    heatmap = await get_gamma_heatmap_async(
        container.async_market_storage, symbol, view, metric, start, end, bucket_seconds, source
    )
    return GammaHeatmapResponse.model_validate(gamma_heatmap_response(heatmap))


@router.get(
    "/flow/{symbol}/minutes",
    response_model=MinuteFlowResponse,
    summary="Signed option delta flow per ET minute",
    description=(
        "Reads minute_option_flow (written by the stream processor: simple option trades classified against the prevailing bid/ask, in delta-equivalent contracts, "
        "calls and puts apart). An estimate of customer flow, not inventory or the market maker's position. Defaults to today (ET), at most 5 days per request. ES/NQ read SPX/NDX."
    ),
)
async def minute_flow(
    symbol: str,
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
) -> MinuteFlowResponse:
    container: Container = request.app.state.container
    reading = (proxy_symbol_for(symbol) or symbol).upper()
    today_start = datetime.now(EASTERN_TIME).replace(hour=0, minute=0, second=0, microsecond=0)
    end = end or datetime.now(timezone.utc)
    start = start or today_start
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=422, detail="start and end must include a timezone (e.g. ...Z)")
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    if end - start > HEATMAP_MAX_RANGE:
        raise HTTPException(status_code=422, detail="the minute flow range is limited to 5 days per request")
    minutes = await container.async_market_storage.get_minute_option_flow(reading, start, end)
    return MinuteFlowResponse.model_validate(minute_flow_response(symbol, minutes))


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
