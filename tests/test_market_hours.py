from __future__ import annotations

from datetime import UTC, date, datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import pytest

from backend.domain.entities import DomainError, MarketHoliday, MarketHolidayType
from backend.domain.use_cases import is_market_open

NEW_YORK = ZoneInfo("America/New_York")


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=NEW_YORK)


def test_open_during_a_regular_weekday_session() -> None:
    # Wednesday, mid-session.
    assert is_market_open(_et(2026, 2, 4, 12, 0)) is True


def test_open_exactly_at_the_9_30_open() -> None:
    assert is_market_open(_et(2026, 2, 4, 9, 30)) is True


def test_closed_exactly_at_the_4_00_close() -> None:
    # Half-open interval — 4:00pm itself is already closed.
    assert is_market_open(_et(2026, 2, 4, 16, 0)) is False


def test_closed_one_minute_before_the_open() -> None:
    assert is_market_open(_et(2026, 2, 4, 9, 29)) is False


def test_closed_one_minute_after_the_close() -> None:
    assert is_market_open(_et(2026, 2, 4, 16, 1)) is False


def test_closed_overnight() -> None:
    assert is_market_open(_et(2026, 2, 4, 2, 0)) is False


def test_closed_on_saturday() -> None:
    assert is_market_open(_et(2026, 2, 7, 12, 0)) is False


def test_closed_on_sunday() -> None:
    assert is_market_open(_et(2026, 2, 8, 12, 0)) is False


def test_known_limitation_does_not_account_for_market_holidays() -> None:
    # 2026-01-01 is a Thursday and a market holiday (New Year's Day) — the
    # function has no holiday calendar, so it reports open anyway. This
    # test documents the accepted, non-silent limitation rather than
    # asserting a false correctness the function doesn't provide.
    assert is_market_open(_et(2026, 1, 1, 12, 0)) is True


def test_accepts_a_non_eastern_timezone_and_converts() -> None:
    # 9:00am UTC is 4:00am ET in winter (EST, UTC-5) — closed.
    assert is_market_open(datetime(2026, 2, 4, 9, 0, tzinfo=UTC)) is False
    # 15:00 UTC is 10:00am ET in winter — open.
    assert is_market_open(datetime(2026, 2, 4, 15, 0, tzinfo=UTC)) is True


def test_closed_all_day_on_a_full_close_holiday_when_holidays_are_given() -> None:
    # 2026-01-01 is a Thursday and a real full-close holiday (New Year's
    # Day) — unlike test_known_limitation_does_not_account_for_market_
    # holidays above, this time the caller actually resolved and passed
    # the holiday, so it must now be honored.
    holidays = {
        date(2026, 1, 1): MarketHoliday(
            date=date(2026, 1, 1), closure_type=MarketHolidayType.FULL_CLOSE, open=None, close=None
        )
    }
    assert is_market_open(_et(2026, 1, 1, 12, 0), holidays) is False
    assert is_market_open(_et(2026, 1, 1, 9, 30), holidays) is False


def test_early_close_holiday_narrows_the_session_instead_of_closing_it() -> None:
    # The real Friday after Thanksgiving, 2026: 09:30-13:00 ET instead of
    # the usual 09:30-16:00.
    holidays = {
        date(2026, 11, 27): MarketHoliday(
            date=date(2026, 11, 27),
            closure_type=MarketHolidayType.EARLY_CLOSE,
            open=dtime(9, 30),
            close=dtime(13, 0),
        )
    }
    assert is_market_open(_et(2026, 11, 27, 12, 59), holidays) is True
    assert is_market_open(_et(2026, 11, 27, 13, 0), holidays) is False  # half-open, same as the regular close
    assert is_market_open(_et(2026, 11, 27, 15, 0), holidays) is False  # open under the OLD fixed window, not this one


def test_a_date_with_no_matching_holiday_entry_uses_the_regular_hours() -> None:
    # The holidays map is real (2026-01-01 is in it) but today isn't in
    # it — must fall back to the plain 9:30-16:00 check, not treat an
    # unrelated date as also closed.
    holidays = {
        date(2026, 1, 1): MarketHoliday(
            date=date(2026, 1, 1), closure_type=MarketHolidayType.FULL_CLOSE, open=None, close=None
        )
    }
    assert is_market_open(_et(2026, 2, 4, 12, 0), holidays) is True


def test_weekend_stays_closed_even_if_incorrectly_listed_as_a_holiday() -> None:
    # Defensive: a weekend is never open regardless of what holidays says.
    holidays = {
        date(2026, 2, 7): MarketHoliday(
            date=date(2026, 2, 7),
            closure_type=MarketHolidayType.EARLY_CLOSE,
            open=dtime(9, 30),
            close=dtime(13, 0),
        )
    }
    assert is_market_open(_et(2026, 2, 7, 10, 0), holidays) is False


def test_market_holiday_rejects_an_early_close_missing_its_own_times() -> None:
    # ThetaData's own contract: full_close rows carry null/null, early_
    # close rows always carry real open/close — an early_close with either
    # missing would be malformed data, not a valid domain state.
    with pytest.raises(DomainError):
        MarketHoliday(
            date=date(2026, 11, 27), closure_type=MarketHolidayType.EARLY_CLOSE, open=None, close=None
        )
