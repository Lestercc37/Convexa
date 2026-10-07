from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timezone
from decimal import Decimal

from backend.domain.entities import MarketPrice, MarketSnapshot, OptionChain, UnderlyingKind
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
from backend.domain.use_cases.cumulative_volume import merge_cumulative_volume
from backend.domain.use_cases.errors import NotFoundError
from backend.domain.use_cases.futures_proxy import (  # noqa: F401 -- PRICE_PROXY_SYMBOL_BY_FUTURE is re-exported
    PRICE_PROXY_SYMBOL_BY_FUTURE,
    future_level_offset,
    future_level_offset_async,
    proxy_symbol_for,
    shift_option_chain,
)
from backend.domain.use_cases.flow import SymbolFlowPressure
from backend.domain.use_cases.market_hours import is_market_open


def _is_pure_index(underlying: str) -> bool:
    """True for SPX/NDX/VIX-style pure indices -- see AnchoredVwap's own
    docstring for why Anchored VWAP is structurally not_applicable for
    these, not just provisional."""
    active = ACTIVE_UNDERLYINGS_BY_SYMBOL.get(underlying.upper())
    return active is not None and active.kind == UnderlyingKind.INDEX


def _session_open_price(price_history: list[MarketPrice]) -> Decimal | None:
    """The earliest recorded price this session -- same source
    calculate_atr_range's own `today_open` already uses -- to anchor
    calculate_expected_move's band, instead of letting it recompute
    around whatever `chain.spot_price` is on every call (see that
    function's own docstring). `None` when nothing has been recorded yet
    this session (the same case AtrRange.today_open leaves as None),
    which calculate_expected_move itself falls back on to chain.spot_price."""
    if not price_history:
        return None
    return min(price_history, key=lambda reading: reading.as_of).price


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

# ES (UnderlyingKind.FUTURE) has no working ThetaData price stream/OHLC/
# EOD endpoint at all (see provider.py's own documented gaps), so unlike
# SPX/NDX above (which have a real, streamed spot price and only borrow
# SPY/QQQ's *volume* for VWAP) there is no real price series to anchor a
# VWAP to in the first place -- the chart itself is empty.
#
# NQ was added alongside ES, 2026-09-27, then removed the next morning
# (2026-09-28, real market open): ThetaData's Options subscription does
# not cover futures options at all -- NQ's own option-chain fetch failed
# outright (500 "Expected exactly one quote; got 0"), never worked even
# once in production. ES's own option-chain fetch does return a
# (non-error) result, which is the only reason it's still here -- but see
# the open investigation into whether that result is trustworthy at all
# (its Gamma/Walls have sat at an implausibly small, collapsed scale
# since 2026-09-02).
#
# Confirmed with the user, 2026-09-27: ES and its cash index (SPX) move
# in near lock-step intraday but carry a "basis" -- a few points of
# interest/dividend carry that drifts slowly and isn't knowable from
# Convexa's own data. Rather than guess it, the owner
# reads the real 9:30 ET opening print off their own live futures feed
# (ThinkOrSwim) and enters it once a session (see the future_price_
# anchors table / futures.py's opening-price endpoint); everything
# after that is SPX/NDX's own real, already-streaming price history
# shifted by one constant offset = anchor - proxy's own price at that
# same 9:30 open. See future_price_offset()/get_price_history_async()
# below.
# PRICE_PROXY_SYMBOL_BY_FUTURE lives in futures_proxy.py (imported above) so the gamma/chain
# read models can share it without a circular import.

# How old the stored chain snapshot (written by the scheduler, the only writer)
# may be, during market hours, before GET /chain/{symbol} fetches live instead.
# Tuning knob: the scheduler's snapshots land every ~95 s today (cycle
# 63-76 s + the 30 s sleep), so most polls are past 60 s -- change it with
# data, not here by reflex.
CHAIN_STORED_MAX_AGE_SECONDS = 60


async def future_price_offset(
    storage: IAsyncMarketReadStorage, future_symbol: str, proxy_symbol: str
) -> tuple[Decimal, list[MarketPrice]] | None:
    """(offset, proxy_session_history), or None while either the proxy
    has no price yet this session or the owner hasn't entered today's
    anchor yet -- see PRICE_PROXY_SYMBOL_BY_FUTURE's own docstring.
    Shared by get_price_history_async and get_vwap_history_async so both
    apply the exact same offset to the exact same session's data,
    computed once."""
    latest_proxy_price = await storage.get_latest_price(proxy_symbol)
    if latest_proxy_price is None:
        return None
    session_open = calculate_session_open(latest_proxy_price.as_of)
    anchor = await storage.get_future_price_anchor(future_symbol, session_open.date())
    if anchor is None:
        return None
    proxy_history = await storage.get_price_history(
        proxy_symbol, session_open, latest_proxy_price.as_of
    )
    if not proxy_history:
        return None
    offset = anchor - proxy_history[0].price
    return offset, proxy_history


async def get_price_history_async(
    storage: IAsyncMarketReadStorage, underlying: str
) -> list[MarketPrice]:
    """Every point to plot for `underlying` since its most recently
    traded session's 09:30 ET open -- GET /market/{symbol}/history's own
    read model (kept here, not inlined in the route, so it's the one
    thing get_vwap_history_async's own future-proxy branch below can
    share). ES/NQ synthesize this from their proxy's own real history
    (see future_price_offset) instead of reading their own (nonexistent)
    MarketPrice rows."""
    symbol = underlying.upper()
    proxy_symbol = PRICE_PROXY_SYMBOL_BY_FUTURE.get(symbol)
    if proxy_symbol is not None:
        result = await future_price_offset(storage, symbol, proxy_symbol)
        if result is None:
            return []
        offset, proxy_history = result
        return [
            replace(point, symbol=symbol, price=point.price + offset)
            for point in proxy_history
        ]
    latest_price = await storage.get_latest_price(symbol)
    if latest_price is None:
        return []
    return await storage.get_price_history(
        symbol, calculate_session_open(latest_price.as_of), latest_price.as_of
    )


def get_option_chain(
    storage: IStorage,
    provider: IDataProvider,
    underlying: str,
    expiration: date | None = None,
    freshness_seconds: int = CHAIN_STORED_MAX_AGE_SECONDS,
) -> OptionChain:
    if proxy_symbol_for(underlying) is not None:
        # Same rule as the async/expirations paths: a future's chain is its index's, shifted.
        return get_option_chain_expirations(storage, underlying)
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
    # Live fallback: returned to the caller, NEVER persisted. The scheduler is the
    # only writer of option_chain_snapshots -- this process has no trade stream,
    # so its provider reports volume 0 for every contract, and saving that chain
    # filled the table with all-zero-volume snapshots (~48% of SPXW 0DTE rows on
    # 2026-10-06/07, 138 of 289 snapshots on 10-07). The volume is read back from
    # contract_cumulative_volume (the stream processor's export, <= ~15 s old).
    return merge_cumulative_volume(provider.get_option_chain(underlying, expiration), storage)


async def get_option_chain_async(
    async_storage: IAsyncMarketReadStorage,
    sync_storage: IStorage,
    provider: IDataProvider,
    underlying: str,
    expiration: date | None = None,
    freshness_seconds: int = CHAIN_STORED_MAX_AGE_SECONDS,
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

    The live fallback never writes to option_chain_snapshots (the scheduler is the
    only writer) and fills the volume from contract_cumulative_volume -- see
    get_option_chain.
    """
    proxy_symbol = proxy_symbol_for(underlying)
    if proxy_symbol is not None:
        # ES/NQ: the index's chain in the future's points; never a provider fetch for the future
        # itself (ThetaData's "ES" is Eversource Energy, not the E-mini).
        proxy_chain = await get_option_chain_async(
            async_storage, sync_storage, provider, proxy_symbol, expiration, freshness_seconds
        )
        offset = await future_level_offset_async(async_storage, underlying.upper(), proxy_symbol)
        if offset is None:
            raise NotFoundError(
                f"{underlying.upper()}'s option levels come from {proxy_symbol}: enter today's "
                f"{underlying.upper()} opening price first"
            )
        return shift_option_chain(proxy_chain, underlying.upper(), offset)
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
    proxy_symbol = proxy_symbol_for(underlying)
    if proxy_symbol is not None:
        proxy_chain = storage.get_latest_chain_snapshot(proxy_symbol)
        if proxy_chain is None:
            raise NotFoundError(f"No option chain found for {underlying.upper()}")
        offset = future_level_offset(storage, underlying.upper(), proxy_symbol)
        if offset is None:
            raise NotFoundError(
                f"{underlying.upper()}'s option levels come from {proxy_symbol}: enter today's "
                f"{underlying.upper()} opening price first"
            )
        return shift_option_chain(proxy_chain, underlying.upper(), offset)
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
        expected_move=calculate_expected_move(
            chain, price.as_of, session_open_price=_session_open_price(price_history)
        ),
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
        expected_move=calculate_expected_move(
            chain, price.as_of, session_open_price=_session_open_price(price_history)
        ),
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

    Anchored to the latest stored price's OWN `as_of`, not wall-clock
    `now`, same as `build_market_snapshot`/`_async` already do and for
    the same reason `get_price_history`'s own route now is (see that
    route's docstring) -- anchoring to `now` silently returned an empty
    series over a weekend, since "today" (Saturday/Sunday) never had a
    real 09:30 ET session to anchor to in the first place.
    """
    symbol = underlying.upper()
    future_proxy_symbol = PRICE_PROXY_SYMBOL_BY_FUTURE.get(symbol)
    if future_proxy_symbol is not None:
        result = await future_price_offset(storage, symbol, future_proxy_symbol)
        if result is None:
            return [], False
        offset, _proxy_history = result
        series, not_applicable = await get_vwap_history_async(storage, future_proxy_symbol)
        return [(as_of, value + offset) for as_of, value in series], not_applicable

    proxy_symbol = VWAP_PROXY_SYMBOL_BY_INDEX.get(underlying.upper())
    if proxy_symbol is None and _is_pure_index(underlying):
        return [], True
    latest_price = await storage.get_latest_price(underlying)
    if latest_price is None:
        return [], False
    as_of = latest_price.as_of
    session_open = calculate_session_open(as_of)
    if proxy_symbol is not None:
        index_history = await storage.get_price_history(underlying, session_open, as_of)
        proxy_history = await storage.get_price_history(proxy_symbol, session_open, as_of)
        series = calculate_proxy_anchored_vwap_series(index_history, proxy_history, as_of)
        return series, False
    price_history = await storage.get_price_history(underlying, session_open, as_of)
    return calculate_anchored_vwap_series(price_history, as_of), False
