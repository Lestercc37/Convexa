"""Convexa's own implied volatility, phase 1 = SHADOW (Lester 2026-10-23 / 10-26; docs: memory own_iv_engine_plan_2026_10_23).

ThetaData's IV has two known weak spots: deep in-the-money contracts whose mid sits at or below intrinsic come back with IV 0 (gamma silently zeroed,
2026-10-23), and in-the-money quotes with little time value give noisy IVs (2-7 vol points above the out-of-the-money side of the same strike,
2026-10-19). Today the twin rule patches both. This module solves the IV ourselves, per expiration:

1. Forward from put-call parity: for the strikes with both a call and a put quote near the money, F = K + (C - P) * e^(rT); the median of the few
   nearest strikes. No dividend or borrow assumption is needed (SPY / QQQ / IWM / NVDA pay dividends; ThetaData's IV ignores them).
2. Each strike's IV from its OUT-of-the-money side (call at or above F, put below), Black-76 on the forward, solved on the mid by Newton with a
   bisection fallback. Quotes without information (no bid, mid at or below the forward intrinsic, a crossed or empty quote) get no IV.
3. The in-the-money contract of the strike takes the same IV (put-call parity: one volatility per strike and expiration).

Nothing reads this yet: docs/hm_own_iv_compare.py compares it with ThetaData's IV and recomputes the levels offline. Pure Python floats (no numpy on
the server): ~3,000 contracts solve in well under a second."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from statistics import median

FORWARD_PAIRS = 5
MIN_IV = 1e-4
MAX_IV = 5.0
TOLERANCE = 1e-7


@dataclass(frozen=True, slots=True)
class QuoteInput:
    occ_symbol: str
    expiration: date
    strike: float
    is_call: bool
    bid: float
    ask: float


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def black76_price(forward: float, strike: float, sigma: float, years: float, rate: float, is_call: bool) -> float:
    discount = math.exp(-rate * years)
    if sigma <= 0 or years <= 0:
        intrinsic = max(forward - strike, 0.0) if is_call else max(strike - forward, 0.0)
        return discount * intrinsic
    s = sigma * math.sqrt(years)
    d1 = (math.log(forward / strike) + 0.5 * s * s) / s
    d2 = d1 - s
    if is_call:
        return discount * (forward * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return discount * (strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1))


def _vega76(forward: float, strike: float, sigma: float, years: float, rate: float) -> float:
    s = sigma * math.sqrt(years)
    d1 = (math.log(forward / strike) + 0.5 * s * s) / s
    return math.exp(-rate * years) * forward * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) * math.sqrt(years)


def solve_iv(price: float, forward: float, strike: float, years: float, rate: float, is_call: bool) -> float | None:
    """The Black-76 volatility that reproduces `price`, or None when the price carries no volatility (at or below the discounted intrinsic, or above
    what any volatility up to MAX_IV can produce)."""
    if price <= 0 or years <= 0 or forward <= 0 or strike <= 0:
        return None
    lo_price = black76_price(forward, strike, MIN_IV, years, rate, is_call)
    hi_price = black76_price(forward, strike, MAX_IV, years, rate, is_call)
    if price <= lo_price + 1e-12 or price >= hi_price:
        return None
    lo, hi = MIN_IV, MAX_IV
    sigma = min(max(math.sqrt(2 * math.pi / years) * price / forward, 0.05), 2.0)
    for _ in range(100):
        value = black76_price(forward, strike, sigma, years, rate, is_call) - price
        if abs(value) < TOLERANCE:
            return sigma
        if value > 0:
            hi = sigma
        else:
            lo = sigma
        vega = _vega76(forward, strike, sigma, years, rate)
        step = sigma - value / vega if vega > 1e-12 else None
        sigma = step if step is not None and lo < step < hi else 0.5 * (lo + hi)
        if hi - lo < 1e-10:
            return sigma
    return sigma


def _mid(quote: QuoteInput) -> float | None:
    if quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid:
        return None
    return 0.5 * (quote.bid + quote.ask)


def implied_forward(quotes: list[QuoteInput], spot: float, years: float, rate: float) -> float | None:
    """Put-call parity forward of ONE expiration: median over the FORWARD_PAIRS strikes nearest the spot that have both a valid call and put mid."""
    calls = {q.strike: _mid(q) for q in quotes if q.is_call}
    puts = {q.strike: _mid(q) for q in quotes if not q.is_call}
    pairs = sorted((k for k in calls if calls[k] is not None and puts.get(k) is not None), key=lambda k: abs(k - spot))[:FORWARD_PAIRS]
    if not pairs:
        return None
    growth = math.exp(rate * years)
    return median(k + (calls[k] - puts[k]) * growth for k in pairs)


def own_iv_by_contract(quotes: list[QuoteInput], spot: float, years: float, rate: float) -> tuple[dict[str, float], float | None]:
    """Every contract's IV for ONE expiration (strike IV from its out-of-the-money side, shared by both sides), and the forward used."""
    forward = implied_forward(quotes, spot, years, rate)
    if forward is None or years <= 0:
        return {}, forward
    by_strike: dict[float, list[QuoteInput]] = {}
    for quote in quotes:
        by_strike.setdefault(quote.strike, []).append(quote)
    result: dict[str, float] = {}
    for strike, sides in by_strike.items():
        otm_is_call = strike >= forward
        otm = next((q for q in sides if q.is_call == otm_is_call), None)
        mid = _mid(otm) if otm else None
        sigma = solve_iv(mid, forward, strike, years, rate, otm_is_call) if mid is not None else None
        if sigma is None:
            continue
        for quote in sides:
            result[quote.occ_symbol] = sigma
    return result, forward
