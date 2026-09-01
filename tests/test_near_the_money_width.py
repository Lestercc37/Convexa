from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.domain.entities import DailyBar
from backend.domain.use_cases.calculate_near_the_money_width import (
    ATR_WIDTH_MULTIPLIER,
    FIXED_WIDTH_BY_SYMBOL,
    INSUFFICIENT_DATA_WIDTH_FRACTION,
    calculate_near_the_money_width,
)

# 15 consecutive closed days, each with a flat $2 high/low range and a
# constant close — True Range for every day after the first is exactly
# max(2, 0, 0) = 2 (no gap: today's high/low always straddle yesterday's
# close), so ATR = 2 exactly, a known value to assert against.
_FLAT_HIGH = Decimal(101)
_FLAT_LOW = Decimal(99)
_FLAT_CLOSE = Decimal(100)


def _flat_daily_bars(count: int = 15, symbol: str = "SPY") -> list[DailyBar]:
    return [
        DailyBar(
            symbol=symbol,
            date=date(2026, 1, 1) + timedelta(days=offset),
            open_price=_FLAT_CLOSE,
            high=_FLAT_HIGH,
            low=_FLAT_LOW,
            close=_FLAT_CLOSE,
        )
        for offset in range(count)
    ]


def test_vix_uses_a_fixed_width_regardless_of_daily_bars_or_spot() -> None:
    width = calculate_near_the_money_width("VIX", _flat_daily_bars(), Decimal("18.50"))
    assert width == FIXED_WIDTH_BY_SYMBOL["VIX"]


def test_es_uses_a_fixed_width_even_with_no_daily_bars_at_all() -> None:
    # ES's real-world case: get_daily_bars() always returns [] for
    # futures (no ThetaData futures EOD endpoint) -- confirm the fixed
    # width doesn't depend on daily_bars being non-empty.
    width = calculate_near_the_money_width("ES", [], Decimal(5680))
    assert width == FIXED_WIDTH_BY_SYMBOL["ES"]


def test_symbol_matching_is_case_insensitive() -> None:
    width = calculate_near_the_money_width("vix", _flat_daily_bars(), Decimal("18.50"))
    assert width == FIXED_WIDTH_BY_SYMBOL["VIX"]


def test_normal_symbol_uses_atr_times_multiplier() -> None:
    width = calculate_near_the_money_width("SPY", _flat_daily_bars(), Decimal(560))
    assert width == Decimal(2) * ATR_WIDTH_MULTIPLIER
    assert width == Decimal(3)


def test_insufficient_daily_bars_falls_back_to_a_fraction_of_spot() -> None:
    # Fewer than the 15 bars calculate_atr_range requires -- atr is None.
    width = calculate_near_the_money_width("SPY", _flat_daily_bars(count=5), Decimal(560))
    assert width == Decimal(560) * INSUFFICIENT_DATA_WIDTH_FRACTION
    assert width == Decimal("11.2")


def test_insufficient_daily_bars_fallback_scales_with_spot_not_a_flat_dollar_amount() -> None:
    # Same degenerate "not enough data" case for two very differently
    # priced symbols -- the fallback must scale with spot, not reuse one
    # width sized for a completely different symbol.
    low_price_width = calculate_near_the_money_width("AMZN", _flat_daily_bars(count=5), Decimal(200))
    high_price_width = calculate_near_the_money_width("SPX", _flat_daily_bars(count=5), Decimal(5600))
    assert high_price_width > low_price_width
    assert high_price_width == Decimal(5600) * INSUFFICIENT_DATA_WIDTH_FRACTION
