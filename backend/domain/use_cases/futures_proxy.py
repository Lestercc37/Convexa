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

from dataclasses import replace
from datetime import date
from decimal import Decimal

from backend.domain.entities import GammaAggregate, MarketSnapshot, OptionChain
from backend.domain.use_cases.calculate_anchored_vwap import calculate_session_open

# future -> the index whose own data stands in for it.
PRICE_PROXY_SYMBOL_BY_FUTURE: dict[str, str] = {
    "ES": "SPX",
    "NQ": "NDX",
}

# (proxy, session date) -> the proxy's first price of that session. It never changes once the
# session has started, so it is read once instead of loading the session's whole price history on
# every gamma request.
_proxy_open_cache: dict[tuple[str, date], Decimal] = {}


def proxy_symbol_for(symbol: str) -> str | None:
    return PRICE_PROXY_SYMBOL_BY_FUTURE.get(symbol.upper())


def _remember_open(proxy_symbol: str, session_date: date, price: Decimal) -> None:
    if len(_proxy_open_cache) > 64:
        _proxy_open_cache.clear()
    _proxy_open_cache[(proxy_symbol, session_date)] = price


def future_level_offset(storage, future_symbol: str, proxy_symbol: str) -> Decimal | None:
    """anchor - proxy's first price of the session, or None while the proxy has no price yet or
    the owner has not entered today's anchor. Sync twin of future_level_offset_async."""
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
        open_price = history[0].price
        _remember_open(proxy_symbol, session_open.date(), open_price)
    return anchor - open_price


async def future_level_offset_async(storage, future_symbol: str, proxy_symbol: str) -> Decimal | None:
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
        open_price = history[0].price
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
