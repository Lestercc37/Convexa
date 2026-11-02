"""5-day replay, phase 1 (backend/domain/use_cases/replay.py, GET /replay/{symbol}/day and /replay/{symbol}/days)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.domain.entities import GammaAggregate, LevelCycle, MarketPrice
from backend.domain.use_cases.replay import bucket_candles, clear_replay_cache, per_minute, session_window, shift_level

DAY = date(2026, 10, 30)  # a Friday, EDT: 09:30 ET = 13:30 UTC
OPEN = datetime(2026, 10, 30, 13, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_replay_cache()
    yield
    clear_replay_cache()


def _price(symbol: str, at: datetime, price: str, volume: int = 0) -> MarketPrice:
    return MarketPrice(symbol=symbol, as_of=at, price=Decimal(price), volume=volume)


def test_the_session_window_is_09_30_to_16_00_et() -> None:
    start, end = session_window(DAY)
    assert start == OPEN
    assert end == OPEN + timedelta(hours=6, minutes=30)


def test_candles_are_bucketed_from_the_open_and_shifted() -> None:
    readings = [
        _price("SPX", OPEN - timedelta(seconds=5), "1"),  # pre-open: dropped
        _price("SPX", OPEN + timedelta(seconds=40), "102"),
        _price("SPX", OPEN + timedelta(seconds=2), "100"),
        _price("SPX", OPEN + timedelta(seconds=30), "99"),
        _price("SPX", OPEN + timedelta(minutes=4, seconds=10), "105"),
        _price("SPX", OPEN + timedelta(minutes=5, seconds=1), "104"),
    ]
    one = bucket_candles(readings, OPEN, 60)
    assert [(c.time, c.open, c.high, c.low, c.close) for c in one[:1]] == [(OPEN, 100, 102, 99, 102)]
    assert [c.time for c in one] == [OPEN, OPEN + timedelta(minutes=4), OPEN + timedelta(minutes=5)]
    five = bucket_candles(readings, OPEN, 300, Decimal("10"))
    assert [(c.open, c.high, c.low, c.close) for c in five] == [(110, 115, 109, 115), (114, 114, 114, 114)]


def test_the_vwap_keeps_the_last_value_of_each_minute() -> None:
    series = [(OPEN + timedelta(seconds=5), Decimal("1")), (OPEN + timedelta(seconds=50), Decimal("2")), (OPEN + timedelta(minutes=1, seconds=3), Decimal("3"))]
    assert per_minute(series, Decimal("1")) == ((OPEN, Decimal("3")), (OPEN + timedelta(minutes=1), Decimal("4")))


def test_levels_shift_by_the_offset_and_missing_ones_stay_missing() -> None:
    cycle = LevelCycle(OPEN, Decimal("7700"), None, Decimal("7650"), None)
    moved = shift_level(cycle, Decimal("30"))
    assert (moved.call_wall, moved.put_wall, moved.absolute_gamma_strike, moved.gamma_flip) == (Decimal("7730"), None, Decimal("7680"), None)


def _seed(client: TestClient) -> None:
    storage = client.app.state.container.storage
    for minute, (spx, spy) in enumerate([("7600", "760.0"), ("7610", "761.0"), ("7605", "760.5")]):
        at = OPEN + timedelta(minutes=minute, seconds=5)
        storage.save_market_price(_price("SPX", at, spx))
        storage.save_market_price(_price("SPY", at, spy, volume=1000 * (minute + 1)))
    storage.save_market_price(_price("SPX", OPEN - timedelta(days=1) + timedelta(minutes=10), "7550"))  # Thursday
    for minute, call_wall in ((0, "7650"), (2, "7660")):
        storage.save_gamma_aggregate(
            GammaAggregate(
                symbol="SPX",
                as_of=OPEN + timedelta(minutes=minute, seconds=20),
                view="tactical",
                call_wall=Decimal(call_wall),
                put_wall=Decimal("7550"),
                gamma_flip=None,
                absolute_gamma_strike=Decimal("0"),
            )
        )


def test_the_day_endpoint_returns_candles_levels_and_the_proxy_vwap() -> None:
    from backend.main import app

    with TestClient(app) as client:
        _seed(client)
        body = client.get("/api/v1/replay/SPX/day?dia=2026-10-30").json()

    assert body["source_symbol"] == "SPX" and body["anchored"] is None and body["price_offset"] == 0
    assert body["live"] is False
    assert [c["close"] for c in body["candles_1m"]] == [7600, 7610, 7605]
    assert body["candles_1m"][0]["time"] == "2026-10-30T13:30:00Z"
    assert len(body["candles_5m"]) == 1 and body["candles_5m"][0]["high"] == 7610
    assert [lv["call_wall"] for lv in body["levels"]] == [7650, 7660]
    assert body["levels"][0]["absolute_gamma_strike"] is None and body["levels"][0]["gamma_flip"] is None
    assert body["vwap_proxy_symbol"] == "SPY" and len(body["vwap"]) == 3
    assert body["vwap"][0]["value"] == pytest.approx(7600)


def test_es_is_spx_shifted_by_that_days_anchor_or_the_index_without_one() -> None:
    from backend.main import app

    with TestClient(app) as client:
        _seed(client)
        unanchored = client.get("/api/v1/replay/ES/day?dia=2026-10-30").json()
        clear_replay_cache()
        client.app.state.container.storage.set_future_price_anchor("ES", DAY, Decimal("7640"))
        anchored = client.get("/api/v1/replay/ES/day?dia=2026-10-30").json()

    assert unanchored["anchored"] is False and unanchored["price_offset"] == 0
    assert [c["close"] for c in unanchored["candles_1m"]] == [7600, 7610, 7605]
    # Offset = anchor - the index's opening reference (median of its first minute: 7600 and 7610), as the live chart uses.
    assert anchored["anchored"] is True and anchored["price_offset"] == 35
    assert anchored["source_symbol"] == "SPX"
    assert [c["close"] for c in anchored["candles_1m"]] == [7635, 7645, 7640]
    assert [lv["call_wall"] for lv in anchored["levels"]] == [7685, 7695]


def test_the_days_endpoint_lists_earlier_sessions_newest_first(monkeypatch) -> None:
    from backend.main import app

    monkeypatch.setattr("backend.domain.use_cases.replay.datetime", _FrozenDatetime)
    with TestClient(app) as client:
        _seed(client)
        body = client.get("/api/v1/replay/ES/days").json()

    assert body["today"] == "2026-11-02"
    assert body["today_has_data"] is False
    assert body["sessions"] == ["2026-10-30", "2026-10-29"]
    assert body["symbol"] == "ES"


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime(2026, 11, 2, 12, 0, tzinfo=UTC).astimezone(tz) if tz else datetime(2026, 11, 2, 12, 0)
