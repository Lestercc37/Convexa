from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.providers.mock.provider import MockDataProvider
from backend.domain.entities import DailyBar, MarketPrice
from backend.domain.use_cases.calculate_atr_range import REQUIRED_DAILY_BARS, calculate_atr_range
from backend.main import app

# 15 consecutive closed days (day0..day14). day0 supplies only a prior close;
# True Range is computed for day1..day14 (14 values):
#   - day1..day6 and day8..day14: flat close, range-only True Range = 2 each
#     (13 values of 2)
#   - day7: a gap day where the close-to-close terms exceed the day's own
#     high-low range, proving the formula takes max() of all three terms,
#     not just the range: True Range = max(6, 10, 16) = 16
# Sum = 13*2 + 16 = 42 -> ATR = 42 / 14 = 3
_FLAT_BAR = {"high": Decimal("101"), "low": Decimal("99"), "close": Decimal("100")}
_POST_GAP_BAR = {"high": Decimal("86"), "low": Decimal("84"), "close": Decimal("85")}


def _flat_bar(day_offset: int, **overrides: Decimal) -> DailyBar:
    values = {**_FLAT_BAR, **overrides}
    return DailyBar(
        symbol="SPY",
        date=date(2026, 1, 1) + timedelta(days=day_offset),
        open_price=values["close"],
        high=values["high"],
        low=values["low"],
        close=values["close"],
    )


def _known_daily_bars() -> list[DailyBar]:
    bars = [_flat_bar(0)]  # day0: only supplies a prior close
    bars += [_flat_bar(offset) for offset in range(1, 7)]  # day1..day6, TR=2 each
    bars.append(  # day7: gap day, TR=16
        DailyBar(
            symbol="SPY",
            date=date(2026, 1, 1) + timedelta(days=7),
            open_price=Decimal("88"),
            high=Decimal("90"),
            low=Decimal("84"),
            close=Decimal("85"),
        )
    )
    bars += [
        _flat_bar(offset, **_POST_GAP_BAR) for offset in range(8, 15)
    ]  # day8..day14, TR=2 each
    return bars


def _today_reading(price: str) -> list[MarketPrice]:
    return [
        MarketPrice(
            symbol="SPY",
            as_of=datetime(2026, 1, 16, 13, 30, tzinfo=UTC),
            price=Decimal(price),
            volume=1000,
        )
    ]


def test_calculate_atr_range_computes_true_range_and_atr_from_known_bars() -> None:
    result = calculate_atr_range(_known_daily_bars(), _today_reading("90"))

    assert result.atr == Decimal(3)
    assert result.atr_provisional is False
    assert result.daily_bars_count == 15
    # A plain range-only average (ignoring the gap day's larger close-to-close
    # terms) would understate this: (13*2 + 6) / 14 = 32/14, not 3 — confirms
    # the max() of all three True Range terms drives the result.
    assert result.atr != (Decimal(13 * 2 + 6) / Decimal(14))


def test_calculate_atr_range_bands_anchor_to_todays_open() -> None:
    result = calculate_atr_range(_known_daily_bars(), _today_reading("90"))

    assert result.today_open == Decimal(90)
    assert result.bands_provisional is False
    assert result.outer_upper_band == Decimal(93)
    assert result.outer_lower_band == Decimal(87)
    assert result.inner_upper_band == Decimal("91.5")
    assert result.inner_lower_band == Decimal("88.5")


def test_calculate_atr_range_uses_earliest_reading_as_todays_open() -> None:
    readings = [
        MarketPrice(
            symbol="SPY",
            as_of=datetime(2026, 1, 16, 14, 0, tzinfo=UTC),
            price=Decimal("95"),
            volume=500,
        ),
        MarketPrice(
            symbol="SPY",
            as_of=datetime(2026, 1, 16, 13, 30, tzinfo=UTC),  # earliest: session open
            price=Decimal("90"),
            volume=200,
        ),
    ]

    result = calculate_atr_range(_known_daily_bars(), readings)

    assert result.today_open == Decimal(90)


def test_calculate_atr_range_is_provisional_with_insufficient_daily_bars() -> None:
    insufficient_bars = _known_daily_bars()[:14]  # one short of the required 15

    result = calculate_atr_range(insufficient_bars, _today_reading("90"))

    assert result.atr is None
    assert result.atr_provisional is True
    assert result.daily_bars_count == 14
    # Bands can't be computed without an ATR, even though today's open is known.
    assert result.bands_provisional is True
    assert result.today_open == Decimal(90)
    assert result.outer_upper_band is None
    assert result.outer_lower_band is None
    assert result.inner_upper_band is None
    assert result.inner_lower_band is None


def test_calculate_atr_range_bands_provisional_without_todays_reading() -> None:
    result = calculate_atr_range(_known_daily_bars(), [])

    # ATR itself is ready — only the bands, which need today's open, are not.
    assert result.atr == Decimal(3)
    assert result.atr_provisional is False
    assert result.today_open is None
    assert result.bands_provisional is True
    assert result.outer_upper_band is None
    assert result.outer_lower_band is None
    assert result.inner_upper_band is None
    assert result.inner_lower_band is None


def test_calculate_atr_range_ignores_bar_order_and_extra_history() -> None:
    extra_old_bar = _flat_bar(-1)
    shuffled = [extra_old_bar, *reversed(_known_daily_bars())]

    result = calculate_atr_range(shuffled, _today_reading("90"))

    assert result.daily_bars_count == 16
    assert result.atr == Decimal(3)


def test_mock_provider_generates_deterministic_daily_bars() -> None:
    provider = MockDataProvider()

    first_call = provider.get_daily_bars("SPY")
    second_call = provider.get_daily_bars("SPY")

    assert first_call == second_call
    assert len(first_call) >= REQUIRED_DAILY_BARS
    dates = [bar.date for bar in first_call]
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates)  # one bar per day, no duplicates


def test_mock_provider_daily_bars_feed_a_non_provisional_atr() -> None:
    bars = MockDataProvider().get_daily_bars("SPY")

    result = calculate_atr_range(bars, _today_reading("90"))

    assert result.atr_provisional is False
    assert result.atr is not None


def test_trigger_calculation_persists_daily_bars() -> None:
    with TestClient(app) as client:
        client.post("/internal/trigger-calculation/spy")
        storage = client.app.state.container.storage
        stored_bars = storage.get_daily_bars("SPY", limit=REQUIRED_DAILY_BARS)

    assert len(stored_bars) == REQUIRED_DAILY_BARS
    assert all(bar.symbol == "SPY" for bar in stored_bars)
