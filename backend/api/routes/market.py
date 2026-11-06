from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError

from backend.api.schemas import (
    MarketSnapshotResponse,
    PriceCandlesResponse,
    PriceHistoryResponse,
    ReplayDayResponse,
    ReplayDaysResponse,
    VwapHistoryResponse,
    PriceSourceResponse,
    PriceSourcesResponse,
)
from backend.api.serializers import (
    market_response,
    price_candles_response,
    price_history_response,
    vwap_history_response,
)
from backend.api.response_cache import TtlSingleFlight
from backend.core.container import Container
from backend.domain.entities import GammaView, PriceCandle
from backend.domain.use_cases.index_stale_guard import STATUS_STALE
from backend.domain.use_cases.read_models import MAX_SESSIONS_1M
from backend.domain.use_cases.replay import ReplayDay, get_replay_day, get_replay_days
from backend.domain.use_cases import (
    build_market_snapshot_async,
    get_price_candles_async,
    get_price_history_async,
    get_vwap_history_async,
)

router = APIRouter(tags=["market"])

# One answer per symbol for a few seconds, shared by every window and user (see response_cache.py). The page polls it every 30 s and the price itself
# arrives by the stream, so 3 s of staleness is invisible.
MARKET_CACHE_SECONDS = 3.0
# Held on the app next to its container: a new container (an app restart, each test client's lifespan) starts with an empty cache.
def _market_cache(request: Request, container: Container) -> TtlSingleFlight[MarketSnapshotResponse]:
    held = getattr(request.app.state, "market_cache", None)
    if held is None or held[0] is not container:
        held = (container, TtlSingleFlight(MARKET_CACHE_SECONDS))
        request.app.state.market_cache = held
    return held[1]


@router.get(
    "/price-sources",
    response_model=PriceSourcesResponse,
    summary="Indices whose price is currently derived from a proxy",
    description=(
        "SPX / NDX replaced by SPY / QQQ x the last good ratio while ThetaData's index feed lags (backend/domain/use_cases/index_stale_guard.py, "
        "2026-11-06). Empty when every index is its own. ES / NQ follow SPX / NDX."
    ),
)
async def get_price_sources(request: Request) -> PriceSourcesResponse:
    container: Container = request.app.state.container
    now = datetime.now(UTC)
    replaced = [
        PriceSourceResponse(
            symbol=status.symbol,
            proxy=status.proxy,
            ratio=status.ratio,
            reason=status.reason,
            since=status.since.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        )
        for status in await container.async_market_storage.get_index_price_fallbacks()
        # A row the stream processor stopped refreshing is not trusted (it stopped mid-episode).
        if status.active and now - status.updated_at <= STATUS_STALE
    ]
    return PriceSourcesResponse(schema_version=1, replaced=replaced)


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

    async def build() -> MarketSnapshotResponse:
        snapshot = await build_market_snapshot_async(container.async_market_storage, symbol)
        return MarketSnapshotResponse.model_validate(market_response(snapshot))

    return await _market_cache(request, container).get(symbol.upper(), build)


@router.get(
    "/market/{symbol}/history",
    response_model=PriceHistoryResponse,
    summary="Get today's session price history",
)
async def get_price_history(symbol: str, request: Request) -> PriceHistoryResponse:
    """Every point `market_snapshots` holds for `symbol` since its most
    recently traded session's 09:30 ET open -- the same data
    `build_market_snapshot` already reads internally for anchored
    VWAP/ATR, just exposed directly this time. Lets the frontend seed the
    chart's candles with everything already formed that session instead
    of starting from an empty chart on mount/symbol change (see
    dashboard.tsx's own comment on `pricePoints`).

    Anchored to the latest stored price's OWN `as_of`, not wall-clock
    `now`, same as `build_market_snapshot`/`_async` already do -- fixes a
    real bug found live, 2026-09-26: anchoring to `now` meant a weekend
    visit computed "today" (Saturday/Sunday)'s own 09:30 ET open, a
    session that never happened, so the query window held zero real
    readings and Friday's candles silently vanished until Monday's first
    real one arrived. A symbol's actual last session -- whenever that
    was -- is always the right thing to show, not "today" specifically.

    ES/NQ go through get_price_history_async's own proxy branch instead
    of reading their own (nonexistent) history -- see
    PRICE_PROXY_SYMBOL_BY_FUTURE's docstring.
    """
    container: Container = request.app.state.container
    points = await get_price_history_async(container.async_market_storage, symbol)
    return PriceHistoryResponse.model_validate(price_history_response(symbol, points))


@router.get(
    "/market/{symbol}/candles",
    response_model=PriceCandlesResponse,
    summary="Get regular-session OHLC candles of the last N trading dates",
)
async def get_price_candles(
    symbol: str,
    request: Request,
    timeframe: Literal["1m", "5m", "15m", "30m", "1h"] = Query(default="5m"),
    sessions: int = Query(default=5, ge=1, le=10),
) -> PriceCandlesResponse:
    """The multi-session view of the chart ("Semana"): candles aggregated in SQL from the stored prices, regular session only (09:30 to
    16:00 ET), bucketed from 09:30 ET. Day mode keeps using /market/{symbol}/history. 404 for ES/NQ (see get_price_candles_async)."""
    if timeframe == "1m" and sessions > MAX_SESSIONS_1M:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("query", "sessions"), "msg": f"1m candles are limited to {MAX_SESSIONS_1M} sessions", "input": sessions}]
        )
    container: Container = request.app.state.container
    candles = await get_price_candles_async(container.async_market_storage, symbol, timeframe, sessions)
    return PriceCandlesResponse.model_validate(price_candles_response(symbol, timeframe, sessions, candles))


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
        vwap_history_response(symbol, last_point_per_minute(series), not_applicable)
    )


def last_point_per_minute(series: list[tuple[datetime, Decimal]]) -> list[tuple[datetime, Decimal]]:
    """The last reading of every minute of an ascending series. The chart draws the VWAP line from the last value of each candle (1 minute or
    longer, frontend lib/candles.ts aggregateVwapPoints), so this is the same line; every reading was ~60,000 points, 3.7 MB and ~1 s of
    server time for SPX on 2026-11-05, and the candles of a symbol switch waited for it."""
    kept: list[tuple[datetime, Decimal]] = []
    for moment, value in series:
        if kept and kept[-1][0].replace(second=0, microsecond=0) == moment.replace(second=0, microsecond=0):
            kept[-1] = (moment, value)
        else:
            kept.append((moment, value))
    return kept


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _candles_payload(candles: tuple[PriceCandle, ...]) -> list[dict]:
    return [{"time": _iso(c.time), "open": float(c.open), "high": float(c.high), "low": float(c.low), "close": float(c.close)} for c in candles]


def _replay_day_payload(replay: ReplayDay) -> dict:
    return {
        "schema_version": 1,
        "symbol": replay.symbol,
        "source_symbol": replay.source_symbol,
        "session_date": replay.session_date.isoformat(),
        "view": replay.view,
        "live": replay.live,
        "session_open": _iso(replay.session_open),
        "session_close": _iso(replay.session_close),
        "price_offset": float(replay.price_offset),
        "anchored": replay.anchored,
        "candles_1m": _candles_payload(replay.candles_1m),
        "candles_5m": _candles_payload(replay.candles_5m),
        "levels": [
            {
                "time": _iso(cycle.time),
                "call_wall": _number(cycle.call_wall),
                "put_wall": _number(cycle.put_wall),
                "absolute_gamma_strike": _number(cycle.absolute_gamma_strike),
                "gamma_flip": _number(cycle.gamma_flip),
            }
            for cycle in replay.levels
        ],
        "vwap": [{"time": _iso(stamp), "value": float(value)} for stamp, value in replay.vwap],
        "vwap_not_applicable": replay.vwap_not_applicable,
        "vwap_proxy_symbol": replay.vwap_proxy_symbol,
    }


@router.get(
    "/replay/{symbol}/days",
    response_model=ReplayDaysResponse,
    summary="Sessions the replay can show: today and the last 5 with stored prices",
)
async def replay_days(symbol: str, request: Request) -> ReplayDaysResponse:
    container: Container = request.app.state.container
    days = await get_replay_days(container.async_market_storage, symbol)
    return ReplayDaysResponse.model_validate(
        {
            "schema_version": 1,
            "symbol": days.symbol,
            "today": days.today.isoformat(),
            "today_has_data": days.today_has_data,
            "sessions": [day.isoformat() for day in days.sessions],
        }
    )


@router.get(
    "/replay/{symbol}/day",
    response_model=ReplayDayResponse,
    summary="One stored session for the replay: 1m/5m candles, the levels of every cycle and the anchored VWAP",
    description=(
        "Reads market_snapshots (candles bucketed from 09:30 ET), gamma_aggregates (Call Wall, Put Wall, ABS, Flip of every stored cycle of `view`) "
        "and runs the live chart's anchored-VWAP formula over that day. ES/NQ are SPX/NDX shifted by that day's typed anchor; without one, the index "
        "is returned unshifted with `anchored` false. Read-only; a finished day is cached for 10 minutes, today's for 5 seconds."
    ),
)
async def replay_day(
    symbol: str,
    request: Request,
    dia: date = Query(description="ET session date, YYYY-MM-DD."),
    view: GammaView = Query(default="tactical"),
) -> ReplayDayResponse:
    container: Container = request.app.state.container
    replay = await get_replay_day(container.async_market_storage, symbol, dia, view)
    return ReplayDayResponse.model_validate(_replay_day_payload(replay))
