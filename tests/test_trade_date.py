from __future__ import annotations

from datetime import date, datetime, timedelta

from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.domain.use_cases.trade_date import (
    TRADE_DATE_OPENS_ET,
    next_trade_date_roll,
    trade_date_of,
    trade_date_start,
)


def et(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=EASTERN_TIME)


def test_the_trade_date_opens_at_2015_et() -> None:
    assert TRADE_DATE_OPENS_ET.hour == 20 and TRADE_DATE_OPENS_ET.minute == 15


def test_before_2015_the_trade_date_is_the_calendar_day() -> None:
    assert trade_date_of(et(2026, 10, 13, 0, 0, 1)) == date(2026, 10, 13)  # no longer a reset at midnight
    assert trade_date_of(et(2026, 10, 13, 9, 30)) == date(2026, 10, 13)
    assert trade_date_of(et(2026, 10, 13, 16, 20)) == date(2026, 10, 13)  # stray after-close prints stay with the day
    assert trade_date_of(et(2026, 10, 13, 20, 14, 59)) == date(2026, 10, 13)


def test_from_2015_the_trade_date_is_the_next_calendar_day() -> None:
    assert trade_date_of(et(2026, 10, 13, 20, 15)) == date(2026, 10, 14)
    assert trade_date_of(et(2026, 10, 13, 23, 59, 59)) == date(2026, 10, 14)


def test_weekend_boundaries() -> None:
    assert trade_date_of(et(2026, 10, 9, 20, 15)) == date(2026, 10, 10)  # Friday evening: Saturday, never trades
    assert trade_date_of(et(2026, 10, 11, 20, 15)) == date(2026, 10, 12)  # Sunday evening opens Monday's session


def test_a_midnight_crossing_stays_in_the_same_trade_date() -> None:
    assert trade_date_of(et(2026, 10, 13, 23, 59, 59)) == trade_date_of(et(2026, 10, 14, 0, 0, 1))


def test_trade_date_start_is_2015_of_the_previous_calendar_day() -> None:
    assert trade_date_start(et(2026, 10, 14, 9, 18)) == et(2026, 10, 13, 20, 15)
    assert trade_date_start(et(2026, 10, 13, 21, 0)) == et(2026, 10, 13, 20, 15)
    assert trade_date_start(et(2026, 10, 12, 9, 30)) == et(2026, 10, 11, 20, 15)  # Monday's opened Sunday night


def test_next_roll_is_the_next_2015_strictly_after_now() -> None:
    assert next_trade_date_roll(et(2026, 10, 13, 9, 30)) == et(2026, 10, 13, 20, 15)
    assert next_trade_date_roll(et(2026, 10, 13, 20, 14, 59)) == et(2026, 10, 13, 20, 15)
    assert next_trade_date_roll(et(2026, 10, 13, 20, 15)) == et(2026, 10, 14, 20, 15)
    assert next_trade_date_roll(et(2026, 10, 13, 23, 0)) == et(2026, 10, 14, 20, 15)


def test_utc_inputs_are_converted_to_eastern() -> None:
    utc_2015 = et(2026, 10, 13, 20, 15).astimezone(datetime(2026, 1, 1).astimezone().tzinfo)
    assert trade_date_of(utc_2015) == date(2026, 10, 14)


def test_daylight_saving_changes_keep_the_wall_clock_boundary() -> None:
    # 2026-11-01: clocks go back at 02:00 (EDT -> EST); 2026-03-08: forward.
    assert trade_date_start(et(2026, 11, 2, 9, 30)) == et(2026, 11, 1, 20, 15)
    assert next_trade_date_roll(et(2026, 11, 1, 12, 0)) == et(2026, 11, 1, 20, 15)
    assert trade_date_of(et(2026, 3, 8, 20, 15)) == date(2026, 3, 9)
    assert next_trade_date_roll(et(2026, 3, 8, 12, 0)).utcoffset() == timedelta(hours=-4)  # EDT after the change


def test_before_the_first_trade_date_roll_rows_are_trusted_from_the_calendar_day_start() -> None:
    from backend.domain.use_cases.trade_date import TRADE_DATE_COUNTERS_FROM, volume_rows_since

    assert TRADE_DATE_COUNTERS_FROM == et(2026, 10, 13, 20, 15)
    # the old process exported the whole day's totals until 23:59:48 of 10-12: they must NOT count for 10-13
    assert volume_rows_since(et(2026, 10, 13, 9, 30)) == et(2026, 10, 13, 0, 0)
    assert volume_rows_since(et(2026, 10, 13, 17, 30)) == et(2026, 10, 13, 0, 0)
    assert volume_rows_since(et(2026, 10, 13, 20, 14, 59)) == et(2026, 10, 13, 0, 0)


def test_from_the_first_roll_on_rows_are_trusted_from_the_trade_date_opening() -> None:
    from backend.domain.use_cases.trade_date import volume_rows_since

    assert volume_rows_since(et(2026, 10, 13, 20, 15)) == et(2026, 10, 13, 20, 15)
    assert volume_rows_since(et(2026, 10, 13, 23, 0)) == et(2026, 10, 13, 20, 15)
    assert volume_rows_since(et(2026, 10, 14, 9, 18)) == et(2026, 10, 13, 20, 15)
    assert volume_rows_since(et(2026, 10, 15, 9, 30)) == et(2026, 10, 14, 20, 15)
