from __future__ import annotations

from datetime import UTC, datetime

from backend.scripts import fast_gamma_retention as fgr


def test_cutoff_is_midnight_eastern_of_today_minus_days() -> None:
    # Thursday 2026-10-22 17:00 ET (EDT, UTC-4): keeps 7 days + today -> cutoff Thursday 10-15 00:00 ET = 04:00 UTC
    now = datetime(2026, 10, 22, 21, 0, tzinfo=UTC)
    assert fgr.cutoff_for(now, 7) == datetime(2026, 10, 15, 4, 0, tzinfo=UTC)


def test_cutoff_follows_the_eastern_day_not_the_utc_day() -> None:
    # 2026-10-23 01:30 UTC is still Thursday 21:30 ET: the cutoff must still be Thursday - 7 days
    now = datetime(2026, 10, 23, 1, 30, tzinfo=UTC)
    assert fgr.cutoff_for(now, 7) == datetime(2026, 10, 15, 4, 0, tzinfo=UTC)


def test_cutoff_across_the_november_dst_change() -> None:
    # 2026-11-05 12:00 UTC (EST, UTC-5): 7 days back is 10-29 00:00 EDT = 04:00 UTC (the change happens 11-01)
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    assert fgr.cutoff_for(now, 7) == datetime(2026, 10, 29, 4, 0, tzinfo=UTC)


def test_market_hours_guard() -> None:
    assert fgr.in_market_hours(datetime(2026, 10, 22, 15, 0, tzinfo=UTC))  # 11:00 ET Thursday
    assert not fgr.in_market_hours(datetime(2026, 10, 22, 21, 0, tzinfo=UTC))  # 17:00 ET
    assert not fgr.in_market_hours(datetime(2026, 10, 24, 15, 0, tzinfo=UTC))  # Saturday
    assert fgr.in_market_hours(datetime(2026, 10, 22, 20, 14, tzinfo=UTC))  # 16:14 ET, still blocked
    assert not fgr.in_market_hours(datetime(2026, 10, 22, 20, 15, tzinfo=UTC))  # 16:15 ET


def test_purge_stops_when_nothing_is_left() -> None:
    batches = [20, 20, 7, 0]
    calls: list[int] = []

    def delete_batch(_cutoff: datetime, n: int) -> int:
        calls.append(n)
        return batches.pop(0)

    total, finished = fgr.purge(delete_batch, datetime(2026, 10, 15, tzinfo=UTC), 20, 60.0, sleep=lambda _s: None)
    assert (total, finished) == (47, True)
    assert calls == [20, 20, 20, 20]


def test_purge_respects_the_time_budget_and_pauses_between_batches() -> None:
    now = [0.0]
    slept: list[float] = []

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += 10.0

    total, finished = fgr.purge(lambda _c, _n: 5, datetime(2026, 10, 15, tzinfo=UTC), 5, 25.0, pause_seconds=0.2, clock=clock, sleep=sleep)
    assert finished is False
    assert total == 15  # t=0,10,20 run a batch; at t=30 the budget (25 s) is spent
    assert slept == [0.2, 0.2, 0.2]


def test_refuses_too_few_days_and_market_hours(capsys) -> None:
    assert fgr.main(["--days", "2"]) == 2
    assert "minimum" in capsys.readouterr().out
