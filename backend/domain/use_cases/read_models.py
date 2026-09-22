from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timezone
from decimal import Decimal

from backend.domain.entities import MarketSnapshot, OptionChain, UnderlyingKind
from backend.domain.ports import IAsyncMarketReadStorage, IDataProvider, IStorage
from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL
from backend.domain.use_cases.calculate_anchored_vwap import (
    calculate_anchored_vwap,
    calculate_anchored_vwap_series,
    calculate_proxy_anchored_vwap,
    calculate_proxy_anchored_vwap_series,
    calculate_session_open,
)
from backend.domain.use_cases.calculate_atr_range import REQUIRED_DAILY_BARS, calculate_atr_range
from backend.domain.use_cases.calculate_closing_dynamics import calculate_closing_dynamics
from backend.domain.use_cases.calculate_expected_move import (
    calculate_expected_move,
    calculate_time_to_close_pct,
)
from backend.domain.use_cases.errors import NotFoundError
from backend.domain.use_cases.flow import SymbolFlowPressure
from backend.domain.use_cases.market_hours import is_market_open


def _is_pure_index(underlying: str) -> bool:
    """True for SPX/NDX/VIX-style pure indices -- see AnchoredVwap's own
    docstring for why Anchored VWAP is structurally not_applicable for
    these, not just provisional."""
    active = ACTIVE_UNDERLYINGS_BY_SYMBOL.get(underlying.upper())
    return active is not None and active.kind == UnderlyingKind.INDEX


# Confirmed with the user, 2026-09-21: a real technique traders already
# use for a pure index with no volume of its own -- a liquid, tightly
# correlated ETF that tracks the same underlying basket. VIX deliberately
# has no entry here: VIXY/UVXY track VIX *futures*, not spot VIX, and
# behave very differently (contango/roll cost) -- a proxy VWAP from either
# would be misleading, not just imprecise, so VIX stays not_applicable.
VWAP_PROXY_SYMBOL_BY_INDEX: dict[str, str] = {
    "SPX": "SPY",
    "NDX": "QQQ",
}

DEFAULT_FRESHNESS_SECONDS = 60


def get_option_chain(
    storage: IStorage,
    provider: IDataProvider,
    underlying: str,
    expiration: date | None = None,
    freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
) -> OptionChain:
    chain = storage.get_latest_chain_snapshot(underlying, expiration)
    now = datetime.now(timezone.utc)
    if chain is not None and (
        (now - chain.as_of).total_seconds() <= freshness_seconds or not is_market_open(now)
    ):
        # Outside market hours the scheduler has already gone quiet (same
        # is_market_open gate as CalculateGammaExposureOrchestrator's own
        # storage-only reads) -- serve the stored snapshot no matter its
        # age instead of refreshing live, so this endpoint stops being the
        # one path that still hit the provider after close (confirmed
        # live, 2026-09: it was overwriting a good pre-close snapshot with
        # a degenerate all-zero-gamma one, see calculate_bsm_greeks).
        return chain
    chain = provider.get_option_chain(underlying, expiration)
    storage.save_chain_snapshot(chain)
    return chain


async def get_option_chain_async(
    async_storage: IAsyncMarketReadStorage,
    sync_storage: IStorage,
    provider: IDataProvider,
    underlying: str,
    expiration: date | None = None,
    freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
) -> OptionChain:
    """Async twin of get_option_chain, for GET /chain/{symbol} -- confirmed
    live, 2026-09-22: the plain `def` version of that route shared
    Starlette's threadpool with the scheduler's own concurrent symbol
    refreshes closely enough to hang 40+ seconds and produce real 500s
    under real market-open load (same root cause /gamma/{symbol} was
    already fixed for -- see that route's own comment).

    The common case (a fresh-enough stored snapshot, or market closed --
    same freshness/market-hours check as get_option_chain above) reads
    purely via `async_storage`, no thread involved at all, same as
    /gamma/{symbol}. The rare case -- genuinely stale during market
    hours -- needs a real live ThetaData fetch, and `IDataProvider` has
    no async-native path (a real, larger change left for later, not
    attempted mid-trading-day): falls through to `asyncio.to_thread`
    running the existing sync get_option_chain, the same pattern the
    scheduler itself already uses for every symbol refresh, so this
    doesn't introduce a new way of doing blocking I/O, just reuses the
    one already proven safe.
    """
    chain = await async_storage.get_latest_chain_snapshot(underlying, expiration)
    now = datetime.now(UTC)
    if chain is not None and (
        (now - chain.as_of).total_seconds() <= freshness_seconds or not is_market_open(now)
    ):
        return chain
    return await asyncio.to_thread(
        get_option_chain, sync_storage, provider, underlying, expiration, freshness_seconds
    )


def get_option_chain_expirations(storage: IStorage, underlying: str) -> OptionChain:
    """Storage-only twin of get_option_chain, for callers that only need
    the distinct expiration dates (the option-chain-viewer/Volatility
    Smile dropdown) -- never falls through to a live provider fetch,
    unlike get_option_chain above. Expiration dates are near-static
    within a session (no new one gets added mid-day, none disappears
    until it actually expires), so there's nothing a live fetch would
    give that the last stored snapshot doesn't already have -- confirmed
    live, 2026-09-22: get_option_chain's own live-fetch fallback (gated
    on a 60s freshness window) left this route stuck for 40+ seconds
    waiting on the same thread pool and ThetaData concurrency semaphore
    the scheduler's own cycle was saturating.
    """
    chain = storage.get_latest_chain_snapshot(underlying)
    if chain is None:
        raise NotFoundError(f"No option chain found for {underlying.upper()}")
    return chain


def get_flow(storage: IStorage, underlying: str, since: datetime | None = None, limit: int = 100):
    return storage.get_flow_events(underlying, since, limit)


async def get_symbol_flow_pressure_async(
    storage: IAsyncMarketReadStorage, underlying: str
) -> SymbolFlowPressure:
    """`/flow/{symbol}/pressure`'s own read -- async for the same reason
    as build_market_snapshot_async (see AsyncPostgreSQLStorage's own
    docstring): a plain storage read, no reason to share the scheduler's
    threadpool. Raises NotFoundError when nothing has been persisted yet
    (the Worker hasn't classified a trade for this symbol this session --
    see SymbolFlowPressure's own docstring for when that happens), same
    "no data at all" convention as build_market_snapshot_async above."""
    flow = await storage.get_symbol_flow_pressure(underlying)
    if flow is None:
        raise NotFoundError(f"No net flow pressure found for {underlying}")
    return flow


def build_market_snapshot(storage: IStorage, underlying: str) -> MarketSnapshot:
    price = storage.get_latest_price(underlying)
    if price is None:
        raise NotFoundError(f"No market price found for {underlying}")
    gamma = storage.get_latest_gamma_aggregate(underlying)
    if gamma is None:
        raise NotFoundError(f"No gamma aggregate found for {underlying}")
    chain = storage.get_latest_chain_snapshot(underlying)
    if chain is None:
        raise NotFoundError(f"No option chain found for {underlying}")
    session_open = calculate_session_open(price.as_of)
    price_history = storage.get_price_history(underlying, session_open, price.as_of)
    daily_bars = storage.get_daily_bars(underlying, limit=REQUIRED_DAILY_BARS)
    time_to_close_pct = calculate_time_to_close_pct(price.as_of)
    proxy_symbol = VWAP_PROXY_SYMBOL_BY_INDEX.get(underlying.upper())
    if proxy_symbol is not None:
        proxy_history = storage.get_price_history(proxy_symbol, session_open, price.as_of)
        anchored_vwap = calculate_proxy_anchored_vwap(
            price_history, proxy_history, price.as_of, proxy_symbol
        )
    else:
        anchored_vwap = calculate_anchored_vwap(
            price_history, price.as_of, not_applicable=_is_pure_index(underlying)
        )
    return MarketSnapshot(
        symbol=price.symbol,
        as_of=price.as_of,
        price=price.price,
        volume=price.volume,
        gamma=gamma,
        expected_move=calculate_expected_move(chain, price.as_of),
        anchored_vwap=anchored_vwap,
        atr_range=calculate_atr_range(daily_bars, price_history),
        closing_dynamics=calculate_closing_dynamics(gamma, price.price, time_to_close_pct),
        recent_flow=tuple(storage.get_recent_flow(underlying)),
    )


async def build_market_snapshot_async(
    storage: IAsyncMarketReadStorage, underlying: str
) -> MarketSnapshot:
    """Async twin of `build_market_snapshot` -- same reads, same pure
    calculate_* calls, just awaited so `/market/{symbol}` can run on the
    event loop instead of the scheduler's shared threadpool (see
    AsyncPostgreSQLStorage's own docstring)."""
    price = await storage.get_latest_price(underlying)
    if price is None:
        raise NotFoundError(f"No market price found for {underlying}")
    gamma = await storage.get_latest_gamma_aggregate(underlying)
    if gamma is None:
        raise NotFoundError(f"No gamma aggregate found for {underlying}")
    chain = await storage.get_latest_chain_snapshot(underlying)
    if chain is None:
        raise NotFoundError(f"No option chain found for {underlying}")
    session_open = calculate_session_open(price.as_of)
    price_history = await storage.get_price_history(underlying, session_open, price.as_of)
    daily_bars = await storage.get_daily_bars(underlying, limit=REQUIRED_DAILY_BARS)
    time_to_close_pct = calculate_time_to_close_pct(price.as_of)
    proxy_symbol = VWAP_PROXY_SYMBOL_BY_INDEX.get(underlying.upper())
    if proxy_symbol is not None:
        proxy_history = await storage.get_price_history(proxy_symbol, session_open, price.as_of)
        anchored_vwap = calculate_proxy_anchored_vwap(
            price_history, proxy_history, price.as_of, proxy_symbol
        )
    else:
        anchored_vwap = calculate_anchored_vwap(
            price_history, price.as_of, not_applicable=_is_pure_index(underlying)
        )
    return MarketSnapshot(
        symbol=price.symbol,
        as_of=price.as_of,
        price=price.price,
        volume=price.volume,
        gamma=gamma,
        expected_move=calculate_expected_move(chain, price.as_of),
        anchored_vwap=anchored_vwap,
        atr_range=calculate_atr_range(daily_bars, price_history),
        closing_dynamics=calculate_closing_dynamics(gamma, price.price, time_to_close_pct),
        recent_flow=tuple(await storage.get_recent_flow(underlying)),
    )


async def get_vwap_history_async(
    storage: IAsyncMarketReadStorage, underlying: str
) -> tuple[list[tuple[datetime, Decimal]], bool]:
    """(series, not_applicable) for GET /market/{symbol}/vwap-history --
    lets the frontend seed the VWAP line on mount/symbol-change the same
    way GET /market/{symbol}/history already seeds candles (see that
    route's own docstring), instead of vwapPoints starting empty and
    rebuilding one point per 30s poll every time the component remounts.

    Same not_applicable rule and the exact same calculate_anchored_vwap_series
    formula build_market_snapshot_async uses for the single current
    value -- this is that same series, not a second implementation. A
    symbol with a VWAP_PROXY_SYMBOL_BY_INDEX entry (SPX/NDX) uses
    calculate_proxy_anchored_vwap_series instead -- see that function's
    own docstring -- and is never not_applicable, since it genuinely can
    compute once both sides have a reading. Permissive like
    get_price_history's own route: no readings yet (or not_applicable)
    just means an empty series, never an error.
    """
    now = datetime.now(timezone.utc)
    session_open = calculate_session_open(now)
    proxy_symbol = VWAP_PROXY_SYMBOL_BY_INDEX.get(underlying.upper())
    if proxy_symbol is not None:
        index_history = await storage.get_price_history(underlying, session_open, now)
        proxy_history = await storage.get_price_history(proxy_symbol, session_open, now)
        series = calculate_proxy_anchored_vwap_series(index_history, proxy_history, now)
        return series, False
    if _is_pure_index(underlying):
        return [], True
    price_history = await storage.get_price_history(underlying, session_open, now)
    return calculate_anchored_vwap_series(price_history, now), False
