from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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
