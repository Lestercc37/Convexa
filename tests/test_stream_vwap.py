"""Shadow per-trade session VWAP (use_cases/stream_vwap.py)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.domain.use_cases.stream_vwap import StreamVwap


def _at(hh: int, mm: int, day: int = 27) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=EASTERN_TIME)


def test_vwap_is_price_times_size_over_size_within_the_regular_session() -> None:
    vwap = StreamVwap()
    vwap.observe("SPY", Decimal("766"), 100, _at(9, 31))
    vwap.observe("SPY", Decimal("768"), 300, _at(10, 0))
    assert vwap.value("SPY") == Decimal("767.5")


def test_pre_and_post_market_trades_index_ticks_and_missing_timestamps_are_ignored() -> None:
    vwap = StreamVwap()
    vwap.observe("SPY", Decimal("700"), 1000, _at(9, 29))
    vwap.observe("SPY", Decimal("700"), 1000, _at(16, 0))
    vwap.observe("SPX", Decimal("7700"), 0, _at(10, 0))
    vwap.observe("SPY", Decimal("700"), 1000, None)
    vwap.observe("SPY", Decimal("766"), 10, _at(9, 30))
    assert vwap.value("SPY") == Decimal("766")
    assert vwap.value("SPX") is None


def test_a_new_session_starts_from_zero_and_the_summary_names_each_symbol() -> None:
    vwap = StreamVwap()
    vwap.observe("QQQ", Decimal("700"), 50, _at(15, 59, day=26))
    vwap.observe("QQQ", Decimal("716"), 10, _at(9, 45))
    assert vwap.value("QQQ") == Decimal("716")
    assert vwap.summary().startswith("QQQ 716.0000 (10 sh, 1 trades, last 09:45:00)")
