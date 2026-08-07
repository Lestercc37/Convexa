from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.domain.entities import MarketPrice
from backend.domain.use_cases.calculate_anchored_vwap import (
    calculate_anchored_vwap,
    calculate_session_open,
)

SESSION_OPEN_UTC = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)  # 9:30 ET (EDT, UTC-4)


def _reading(minutes_after_open: int, price: str, volume: int) -> MarketPrice:
    return MarketPrice(
        symbol="SPY",
        as_of=SESSION_OPEN_UTC + timedelta(minutes=minutes_after_open),
        price=Decimal(price),
        volume=volume,
    )


def test_calculate_session_open_returns_930_et_in_utc() -> None:
    as_of = datetime(2026, 8, 3, 19, 5, tzinfo=UTC)

    anchor = calculate_session_open(as_of)

    assert anchor.astimezone(UTC) == SESSION_OPEN_UTC


def test_anchored_vwap_weights_by_delta_volume_not_simple_average() -> None:
    readings = [
        _reading(5, "550", 800),  # first reading: interval volume = 800 (baseline 0)
        _reading(10, "560", 1000),  # interval volume = 1000 - 800 = 200
        _reading(15, "540", 2000),  # interval volume = 2000 - 1000 = 1000
    ]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=15)

    result = calculate_anchored_vwap(readings, as_of)

    # (550*800 + 560*200 + 540*1000) / (800+200+1000) = 1,092,000 / 2,000 = 546
    assert result.value == Decimal(546)
    assert result.provisional is False
    assert result.sample_count == 3
    assert result.anchor_time == SESSION_OPEN_UTC
    # Simple (unweighted) average of the three prices would be 550, not 546 —
    # confirms volume weighting, not a plain average, drives the result.
    assert result.value != (Decimal(550) + Decimal(560) + Decimal(540)) / 3


def test_anchored_vwap_excludes_readings_before_session_open() -> None:
    pre_market = MarketPrice(
        symbol="SPY",
        as_of=SESSION_OPEN_UTC - timedelta(minutes=1),
        price=Decimal(500),
        volume=50_000,
    )
    first_session_reading = _reading(1, "550", 100)
    as_of = SESSION_OPEN_UTC + timedelta(minutes=1)

    result = calculate_anchored_vwap([pre_market, first_session_reading], as_of)

    assert result.sample_count == 1
    assert result.value == Decimal(550)


def test_anchored_vwap_is_provisional_with_no_readings_since_open() -> None:
    as_of = SESSION_OPEN_UTC + timedelta(minutes=1)

    result = calculate_anchored_vwap([], as_of)

    assert result.value is None
    assert result.provisional is True
    assert result.sample_count == 0
    assert result.anchor_time == SESSION_OPEN_UTC


def test_anchored_vwap_is_provisional_when_session_volume_is_zero() -> None:
    as_of = SESSION_OPEN_UTC
    reading = _reading(0, "550", 0)

    result = calculate_anchored_vwap([reading], as_of)

    assert result.value is None
    assert result.provisional is True
    assert result.sample_count == 1


def test_anchored_vwap_clamps_negative_volume_delta_from_session_rollover() -> None:
    # Simulates a stale reading whose cumulative volume drops below the prior
    # one (e.g. a session-boundary glitch) — its interval volume must not go
    # negative and corrupt the weighted sum.
    readings = [
        _reading(5, "550", 1000),
        _reading(10, "560", 400),  # volume dropped: clamp interval volume to 0
        _reading(15, "540", 1400),  # interval volume = 1400 - 400 = 1000
    ]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=15)

    result = calculate_anchored_vwap(readings, as_of)

    # (550*1000 + 560*0 + 540*1000) / (1000+0+1000) = 1,090,000 / 2,000 = 545
    assert result.value == Decimal(545)
    assert result.sample_count == 3
