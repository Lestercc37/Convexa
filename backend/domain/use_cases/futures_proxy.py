"""Index-proxied futures (ES via SPX, NQ via NDX): option levels from the index, in futures points.

ThetaData has no futures at all, and its "ES" root is Eversource Energy (a ~$65 stock with its own
unrelated options), so Convexa never had real option data for the E-mini. The index options the
futures track do exist (SPX, NDX): their gamma levels, walls, max pain and strikes are the same
levels a trader reads on the futures, shifted by the futures-index basis. The basis is the same
constant offset the price chart already uses (the owner's 9:30 ET opening print for the future
minus the index's own first price of the session, see read_models.future_price_offset), so one
anchor per future per session drives the chart, the VWAP and these levels.

Everything here is a READ-side transformation of the proxy's own stored data: nothing is fetched
or computed for the future itself (the scheduler and the stream skip futures).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.domain.entities import GammaAggregate, MarketSnapshot, OptionChain
from backend.domain.use_cases.calculate_anchored_vwap import calculate_session_open, opening_reference
from backend.domain.use_cases.futures_live import LIVE_BASIS_BARS, LIVE_BASIS_FRESH, live_basis, trusted_basis
from backend.domain.use_cases.market_hours import EASTERN_TIME, MARKET_OPEN_ET

# future -> the index whose own data stands in for it.
PRICE_PROXY_SYMBOL_BY_FUTURE: dict[str, str] = {
    "ES": "SPX",
    "NQ": "NDX",
}

# (proxy, session date) -> the proxy's first price of that session. It never changes once the
# session has started, so it is read once instead of loading the session's whole price history on
# every gamma request.
_proxy_open_cache: dict[tuple[str, date], Decimal] = {}

logger = logging.getLogger(__name__)
# The live basis from NinjaTrader (use_cases/futures_live.py, 2026-11-06) replaces the morning anchor offset while it is fresh. It is read
# on every ES / NQ request (chart, levels, heatmap, price stream), so it is cached per future for LIVE_BASIS_CACHE_SECONDS.
LIVE_BASIS_CACHE_SECONDS = 5.0
_live_basis_cache: dict[str, tuple[float, float | None]] = {}
_live_basis_failed_logged = False


def _bars_since(now: datetime) -> datetime:
    return now - LIVE_BASIS_FRESH - timedelta(minutes=LIVE_BASIS_BARS + 2)


def _cached_basis(future: str) -> tuple[bool, float | None]:
    cached = _live_basis_cache.get(future)
    if cached is not None and time.monotonic() - cached[0] < LIVE_BASIS_CACHE_SECONDS:
        return True, cached[1]
    return False, None


def _remember_basis(future: str, basis: float | None) -> float | None:
    _live_basis_cache[future] = (time.monotonic(), basis)
    return basis


def _log_basis_failure() -> None:
    global _live_basis_failed_logged
    if not _live_basis_failed_logged:
        _live_basis_failed_logged = True
        logger.exception("Reading the live futures basis failed; the anchor offset is used (logged once)")


def live_future_basis(storage, future: str) -> float | None:
    """The NinjaTrader basis of `future` right now (None: none fresh, or a storage without the feed). Sync twin of live_future_basis_async."""
    hit, basis = _cached_basis(future)
    if hit:
        return basis
    read = getattr(storage, "get_futures_live_bars", None)
    if read is None:
        return None
    now = datetime.now(timezone.utc)
    try:
        found = live_basis(read(future, _bars_since(now)), now)
    except Exception:
        _log_basis_failure()
        found = None
    return _remember_basis(future, found[0] if found else None)


async def live_future_basis_async(storage, future: str) -> float | None:
    hit, basis = _cached_basis(future)
    if hit:
        return basis
    read = getattr(storage, "get_futures_live_bars", None)
    if read is None:
        return None
    now = datetime.now(timezone.utc)
    try:
        found = live_basis(await read(future, _bars_since(now)), now)
    except Exception:
        _log_basis_failure()
        found = None
    return _remember_basis(future, found[0] if found else None)


def _with_live_basis(future: str, anchor_offset: Decimal | None, live: float | None) -> Decimal | None:
    """The live basis when it is trusted (see futures_live.trusted_basis), the anchor offset otherwise."""
    if trusted_basis(future, live, float(anchor_offset) if anchor_offset is not None else None):
        return Decimal(str(round(live, 2)))
    return anchor_offset


@dataclass(frozen=True, slots=True)
class OpeningPriceWindow:
    """Which session a typed opening price would belong to, and whether it may be saved right now.

    `waiting_reason`: "before_open" (a trading day, earlier than 9:30 ET) or "waiting_first_price" (9:30 has
    passed but the index has not printed today's first price yet). In both cases the number would land on
    the PREVIOUS session, because the session is taken from the index's latest stored price."""

    session_date: date
    accepting: bool
    waiting_reason: str | None = None


def opening_price_window(now: datetime, latest_proxy_as_of: datetime | None) -> OpeningPriceWindow:
    """Weekdays are treated as trading days (no holiday calendar is available to this endpoint): on a
    weekday holiday the index never prints, so saving stays closed all day -- nothing to enter then.
    Weekends: the last session stays open for a late entry or a correction."""
    eastern = now.astimezone(EASTERN_TIME)
    today = eastern.date()
    latest_session = (
        calculate_session_open(latest_proxy_as_of).date() if latest_proxy_as_of is not None else None
    )
    if eastern.weekday() < 5:
        if eastern.time() < MARKET_OPEN_ET:
            return OpeningPriceWindow(today, False, "before_open")
        if latest_session != today:
            return OpeningPriceWindow(today, False, "waiting_first_price")
        return OpeningPriceWindow(today, True)
    if latest_session is None:
        return OpeningPriceWindow(today, False, "waiting_first_price")
    return OpeningPriceWindow(latest_session, True)


def proxy_symbol_for(symbol: str) -> str | None:
    return PRICE_PROXY_SYMBOL_BY_FUTURE.get(symbol.upper())


def _remember_open(proxy_symbol: str, session_date: date, price: Decimal) -> None:
    if len(_proxy_open_cache) > 64:
        _proxy_open_cache.clear()
    _proxy_open_cache[(proxy_symbol, session_date)] = price


def future_level_offset(storage, future_symbol: str, proxy_symbol: str) -> Decimal | None:
    """The future's points minus the proxy's: NinjaTrader's live basis while fresh (2026-11-06), else anchor - proxy's first price of the
    session, or None while neither exists. Sync twin of future_level_offset_async."""
    return _with_live_basis(future_symbol.upper(), _anchor_offset(storage, future_symbol, proxy_symbol), live_future_basis(storage, future_symbol.upper()))


def _anchor_offset(storage, future_symbol: str, proxy_symbol: str) -> Decimal | None:
    latest = storage.get_latest_price(proxy_symbol)
    if latest is None:
        return None
    session_open = calculate_session_open(latest.as_of)
    anchor = storage.get_future_price_anchor(future_symbol, session_open.date())
    if anchor is None:
        return None
    key = (proxy_symbol, session_open.date())
    open_price = _proxy_open_cache.get(key)
    if open_price is None:
        history = storage.get_price_history(proxy_symbol, session_open, latest.as_of)
        if not history:
            return None
        open_price, complete = opening_reference(history, latest.as_of)
        if complete:
            _remember_open(proxy_symbol, session_open.date(), open_price)
    return anchor - open_price


async def future_level_offset_async(storage, future_symbol: str, proxy_symbol: str) -> Decimal | None:
    anchor = await _anchor_offset_async(storage, future_symbol, proxy_symbol)
    return _with_live_basis(future_symbol.upper(), anchor, await live_future_basis_async(storage, future_symbol.upper()))


async def _anchor_offset_async(storage, future_symbol: str, proxy_symbol: str) -> Decimal | None:
    latest = await storage.get_latest_price(proxy_symbol)
    if latest is None:
        return None
    session_open = calculate_session_open(latest.as_of)
    anchor = await storage.get_future_price_anchor(future_symbol, session_open.date())
    if anchor is None:
        return None
    key = (proxy_symbol, session_open.date())
    open_price = _proxy_open_cache.get(key)
    if open_price is None:
        history = await storage.get_price_history(proxy_symbol, session_open, latest.as_of)
        if not history:
            return None
        open_price, complete = opening_reference(history, latest.as_of)
        if complete:
            _remember_open(proxy_symbol, session_open.date(), open_price)
    return anchor - open_price


def _shift(value: Decimal | None, offset: Decimal) -> Decimal | None:
    return None if value is None else value + offset


def shift_gamma_aggregate(gamma: GammaAggregate, symbol: str, offset: Decimal) -> GammaAggregate:
    """The proxy's aggregate expressed in the future's points: every price level (and each item's
    strike) moves by `offset`; exposures (gamma, vega, ...) are the index's own and are kept."""
    return replace(
        gamma,
        symbol=symbol,
        gamma_flip=_shift(gamma.gamma_flip, offset),
        call_wall=_shift(gamma.call_wall, offset),
        put_wall=_shift(gamma.put_wall, offset),
        max_pain=gamma.max_pain + offset if gamma.max_pain else gamma.max_pain,
        absolute_gamma_strike=(
            gamma.absolute_gamma_strike + offset if gamma.absolute_gamma_strike else gamma.absolute_gamma_strike
        ),
        items=tuple(replace(item, strike=item.strike + offset) for item in gamma.items),
    )


def empty_future_aggregate(proxy_gamma: GammaAggregate, symbol: str) -> GammaAggregate:
    """Before today's anchor exists the levels cannot be expressed in futures points: an honest
    empty aggregate (no levels, no strikes) instead of the index's unshifted numbers."""
    return GammaAggregate(symbol=symbol, as_of=proxy_gamma.as_of, view=proxy_gamma.view)


def shift_market_snapshot(snapshot: MarketSnapshot, symbol: str, offset: Decimal) -> MarketSnapshot:
    """The proxy's /market snapshot in the future's points: the price and every price level move by
    `offset`; widths, percentages and exposures are the index's own and are kept. The index options'
    own flow events are in index strikes and the future has none of its own, so they are dropped."""
    expected_move = snapshot.expected_move
    atr_range = snapshot.atr_range
    anchored_vwap = snapshot.anchored_vwap
    closing = snapshot.closing_dynamics
    return replace(
        snapshot,
        symbol=symbol,
        price=snapshot.price + offset,
        gamma=None if snapshot.gamma is None else shift_gamma_aggregate(snapshot.gamma, symbol, offset),
        expected_move=None
        if expected_move is None
        else replace(
            expected_move,
            upper_bound=expected_move.upper_bound + offset,
            lower_bound=expected_move.lower_bound + offset,
        ),
        anchored_vwap=None
        if anchored_vwap is None
        else replace(anchored_vwap, value=_shift(anchored_vwap.value, offset)),
        atr_range=None
        if atr_range is None
        else replace(
            atr_range,
            today_open=_shift(atr_range.today_open, offset),
            outer_upper_band=_shift(atr_range.outer_upper_band, offset),
            outer_lower_band=_shift(atr_range.outer_lower_band, offset),
            inner_upper_band=_shift(atr_range.inner_upper_band, offset),
            inner_lower_band=_shift(atr_range.inner_lower_band, offset),
        ),
        closing_dynamics=None
        if closing is None
        else replace(
            closing,
            magnet_strike=_shift(closing.magnet_strike, offset),
            max_pain=closing.max_pain + offset if closing.max_pain else closing.max_pain,
        ),
        recent_flow=(),
    )


def shift_option_chain(chain: OptionChain, symbol: str, offset: Decimal) -> OptionChain:
    return replace(
        chain,
        symbol=symbol,
        spot_price=chain.spot_price + offset,
        contracts=tuple(
            replace(contract, underlying=symbol, strike=contract.strike + offset) for contract in chain.contracts
        ),
    )
