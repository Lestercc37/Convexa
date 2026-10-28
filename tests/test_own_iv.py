"""Own IV engine, shadow phase (backend/domain/use_cases/own_iv.py)."""

from __future__ import annotations

import math
from datetime import date

import pytest

from backend.domain.use_cases.own_iv import QuoteInput, black76_price, implied_forward, own_iv_by_contract, solve_iv

EXP = date(2026, 10, 28)


def test_solve_iv_recovers_the_volatility_that_priced_the_option() -> None:
    for sigma in (0.08, 0.15, 0.4, 1.2):
        for strike, is_call in ((7700.0, True), (7650.0, False), (7800.0, True)):
            price = black76_price(7720.0, strike, sigma, 2 / 365, 0.04, is_call)
            assert solve_iv(price, 7720.0, strike, 2 / 365, 0.04, is_call) == pytest.approx(sigma, abs=1e-5)


def test_a_price_at_or_below_intrinsic_has_no_volatility() -> None:
    discounted_intrinsic = 20 * math.exp(-0.04 * 1 / 365)
    assert solve_iv(discounted_intrinsic * 0.999, 7720.0, 7700.0, 1 / 365, 0.04, True) is None
    assert solve_iv(0.0, 7720.0, 7800.0, 1 / 365, 0.04, True) is None


def _book(forward: float, sigma_by_strike: dict[float, float], years: float, rate: float, spread: float = 0.1) -> list[QuoteInput]:
    quotes = []
    for strike, sigma in sigma_by_strike.items():
        for is_call in (True, False):
            mid = black76_price(forward, strike, sigma, years, rate, is_call)
            right = "C" if is_call else "P"
            quotes.append(QuoteInput(f"SPXW261028{right}{int(strike * 1000):08d}", EXP, strike, is_call, max(mid - spread / 2, 0.0), mid + spread / 2))
    return quotes


def test_the_forward_comes_from_put_call_parity_not_the_spot() -> None:
    # A dividend-paying ETF: the forward sits below the spot; parity recovers it from the quotes alone.
    quotes = _book(763.4, {760.0: 0.15, 762.0: 0.14, 764.0: 0.13, 766.0: 0.13, 768.0: 0.14}, 30 / 365, 0.04, spread=0.0)
    assert implied_forward(quotes, spot=765.0, years=30 / 365, rate=0.04) == pytest.approx(763.4, abs=1e-6)


def test_each_strike_takes_its_out_of_the_money_iv_on_both_sides() -> None:
    smile = {7600.0: 0.22, 7650.0: 0.18, 7700.0: 0.14, 7750.0: 0.12, 7800.0: 0.13}
    quotes = _book(7710.0, smile, 1 / 365, 0.04, spread=0.0)
    ivs, forward = own_iv_by_contract(quotes, spot=7712.0, years=1 / 365, rate=0.04)
    assert forward == pytest.approx(7710.0, abs=1e-4)
    for strike, sigma in smile.items():
        call = f"SPXW261028C{int(strike * 1000):08d}"
        put = f"SPXW261028P{int(strike * 1000):08d}"
        assert ivs[call] == pytest.approx(sigma, abs=1e-4)
        assert ivs[put] == ivs[call]  # one volatility per strike: the ITM side never gets a noisy or zero IV of its own


def test_a_strike_without_a_usable_otm_quote_gets_no_iv() -> None:
    quotes = _book(7710.0, {7700.0: 0.14, 7750.0: 0.12}, 1 / 365, 0.04, spread=0.0)
    quotes = [q if not (q.strike == 7750.0 and q.is_call) else QuoteInput(q.occ_symbol, q.expiration, q.strike, q.is_call, 0.0, 0.05) for q in quotes]
    ivs, _ = own_iv_by_contract(quotes, spot=7712.0, years=1 / 365, rate=0.04)
    assert "SPXW261028C07750000" not in ivs and "SPXW261028P07750000" not in ivs
    assert "SPXW261028C07700000" in ivs
