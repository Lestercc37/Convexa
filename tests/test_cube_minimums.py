"""Suggested minimum size of the alert cubes per symbol (item 12b): about 40 cubes a session for every symbol, from the TARGET-th largest non-FAR alert of each completed session,
median of the last sessions, rounded to a 1 / 2 / 5 x 10^k number."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.domain.use_cases import cube_minimums as cm
from backend.domain.use_cases.flow import Moneyness, WhaleAlert, WhaleAlertType
from backend.main import app

TODAY = date(2026, 10, 22)


def _alert(day: date, minute: int, size: float, moneyness: Moneyness = Moneyness.ATM) -> WhaleAlert:
    # 10:00 ET + `minute` (EDT = UTC-4); size = contracts x delta with delta 0.5
    when = datetime(day.year, day.month, day.day, 14, 0, tzinfo=UTC) + timedelta(minutes=minute)
    return WhaleAlert(
        symbol="SPY",
        occ_symbol="SPY261022C00700000",
        alert_type=WhaleAlertType.UNUSUAL,
        amount=Decimal(100000),
        as_of=when,
        estimated_buy_volume=Decimal(1),
        estimated_sell_volume=Decimal(0),
        moneyness=moneyness,
        delta=Decimal("0.5"),
        contracts=int(size * 2),
    )


def _session(day: date, sizes: list[float], moneyness: Moneyness = Moneyness.ATM) -> list[WhaleAlert]:
    return [_alert(day, index % 300, size, moneyness) for index, size in enumerate(sizes)]


def test_nice_number_rounds_to_one_two_or_five_times_a_power_of_ten() -> None:
    assert [cm.nice_number(value) for value in (0, 0.4, 1, 20, 23, 469, 987, 2577, 2112, 3800, 8662)] == [1, 1, 1, 20, 20, 500, 1000, 2000, 2000, 5000, 10000]


def test_the_target_th_largest_alert_of_each_completed_session_sets_the_minimum() -> None:
    sizes = [float(size) for size in range(1000, 0, -10)]  # 100 alerts: 1000, 990, ... 10 -> the 40th largest is 610
    alerts = _session(date(2026, 10, 21), sizes) + _session(date(2026, 10, 20), sizes)
    assert cm.suggested_minimum(alerts, TODAY) == 500  # 610 -> nearest 1/2/5 number in log scale


def test_median_across_sessions_and_only_the_last_five_sessions() -> None:
    def day(n: int, top: float) -> list[WhaleAlert]:
        return _session(date(2026, 10, 21) - timedelta(days=n), [top] * 40)

    alerts = day(0, 200) + day(1, 200) + day(2, 200) + day(3, 5000) + day(4, 5000) + day(5, 100000)  # the 6th (oldest) day is ignored
    assert cm.suggested_minimum(alerts, TODAY) == 200  # median of [200, 200, 200, 5000, 5000]


def test_today_far_alerts_and_alerts_without_size_never_count() -> None:
    today = _session(TODAY, [9999.0] * 60)
    far = _session(date(2026, 10, 21), [9999.0] * 60, Moneyness.FAR)
    no_size = [
        WhaleAlert(
            symbol="SPY", occ_symbol="SPY261022C00700000", alert_type=WhaleAlertType.UNUSUAL, amount=Decimal(1), as_of=datetime(2026, 10, 21, 14, 0, tzinfo=UTC),
            estimated_buy_volume=Decimal(1), estimated_sell_volume=Decimal(0),
        )
        for _ in range(60)
    ]
    assert cm.suggested_minimum(today + far + no_size, TODAY) is None


def test_a_symbol_with_too_few_alerts_per_session_gets_no_suggestion() -> None:
    assert cm.suggested_minimum(_session(date(2026, 10, 21), [100.0] * 39), TODAY) is None


class _Storage:
    def __init__(self, by_symbol: dict[str, list[WhaleAlert]]) -> None:
        self.by_symbol = by_symbol
        self.calls: list[tuple[str, int]] = []

    async def get_recent_whale_alerts(self, underlying: str, limit: int = 100) -> list[WhaleAlert]:
        self.calls.append((underlying, limit))
        return self.by_symbol.get(underlying, [])


def test_the_use_case_answers_per_symbol_and_caches_for_an_hour() -> None:
    cm.reset_cache()
    storage = _Storage({"SPY": _session(date(2026, 10, 21), [2600.0] * 40)})
    now = datetime(2026, 10, 22, 21, 0, tzinfo=UTC)
    first = asyncio.run(cm.get_cube_minimums_async(storage, now))
    assert first == {"schema_version": 1, "target_per_session": 40, "sessions": 5, "minimums": {"SPY": 2000}}
    calls = len(storage.calls)
    assert calls == len(cm.CUBE_SYMBOLS)
    asyncio.run(cm.get_cube_minimums_async(storage, now))
    assert len(storage.calls) == calls  # served from the cache
    cm.reset_cache()


def test_endpoint_answers_an_empty_map_when_nothing_is_stored() -> None:
    cm.reset_cache()
    with TestClient(app) as client:
        response = client.get("/api/v1/alert-cubes/minimums")
    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "target_per_session": 40, "sessions": 5, "minimums": {}}
    cm.reset_cache()
