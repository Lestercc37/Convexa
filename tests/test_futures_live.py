from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import require_session
from backend.api.routes import futures_anchor_feed
from backend.core.sessions import SessionPayload
from backend.domain.entities import MarketPrice
from backend.domain.use_cases import futures_proxy
from backend.domain.use_cases.futures_live import (
    FuturesLiveBar,
    contract_month,
    is_front,
    live_basis,
    normalize_live_future,
    trusted_basis,
)
from backend.domain.use_cases.index_stale_guard import IndexPriceFallback
from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.main import app

KEY = {"X-Convexa-Key": "test-key-not-real"}


def _bar(minute: datetime, close: str, index: str | None, contract: str = "ES 12-26", session: str = "rth") -> FuturesLiveBar:
    price = Decimal(close)
    return FuturesLiveBar("ES", contract, minute, price, price, price, price, 100, session, None if index is None else Decimal(index), minute)


def test_names_contracts_and_the_front_contract() -> None:
    assert [normalize_live_future(n) for n in ("ES", "MES 12-26", "nq", "MNQ", "RTY 12-26", "M2K", "CL 12-26")] == ["ES", "ES", "NQ", "NQ", "RTY", "RTY", None]
    assert contract_month("ES 12-26") == (2026, 12)
    assert contract_month("ES") is None
    # Dec 2026 is front until its roll on 2026-12-10 (8 days before the 18th), then Mar 2027.
    assert is_front("ES 12-26", date(2026, 11, 6)) and not is_front("ES 03-27", date(2026, 11, 6))
    assert is_front("ES 03-27", date(2026, 12, 10)) and not is_front("ES 12-26", date(2026, 12, 10))


def test_the_live_basis_is_the_median_of_the_last_fresh_regular_hours_front_bars() -> None:
    start = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
    bars = [_bar(start + timedelta(minutes=i), str(7430 + i), str(7400 + i - (1 if i == 7 else 0))) for i in range(8)]
    bars.append(_bar(start + timedelta(minutes=8), "7500", None))  # no index price: never used
    bars.append(_bar(start + timedelta(minutes=8), "7600", "7400", contract="ES 03-27"))  # not the front contract
    basis, used, newest = live_basis(bars, start + timedelta(minutes=9))
    assert (basis, used, newest) == (30.0, 5, start + timedelta(minutes=7))
    # NinjaTrader stopped 15 minutes ago: nothing fresh.
    assert live_basis(bars, start + timedelta(minutes=25)) is None


def test_a_live_basis_far_from_the_anchor_is_not_trusted() -> None:
    assert trusted_basis("ES", 31.2, 26.0)
    assert not trusted_basis("ES", 60.0, 26.0)
    assert trusted_basis("ES", 31.2, None)
    assert not trusted_basis("ES", None, 26.0)


@pytest.fixture
def feed(tmp_path, monkeypatch):
    key_file = tmp_path / "key.txt"
    key_file.write_text("test-key-not-real", encoding="ascii")
    monkeypatch.setattr(futures_anchor_feed, "get_settings", lambda: SimpleNamespace(ninjatrader_anchor_key_file=str(key_file)))
    futures_proxy._live_basis_cache.clear()
    with TestClient(app) as client:
        yield client
    futures_proxy._live_basis_cache.clear()


def _minute(now: datetime, back: int) -> datetime:
    return (now - timedelta(minutes=back)).replace(second=0, microsecond=0)


def test_the_feed_needs_the_key_and_skips_a_quote_with_a_wrong_clock(feed) -> None:
    now = datetime.now(UTC)
    quote = {"symbol": "ES", "contract": "ES 12-26", "last": 7445.25, "time": now.isoformat()}
    assert feed.post("/api/v1/feeds/futures-live", json={"quotes": [quote]}).status_code == 401
    late = dict(quote, time=(now - timedelta(minutes=2)).isoformat())
    result = feed.post("/api/v1/feeds/futures-live", json={"quotes": [late]}, headers=KEY).json()
    assert result["quotes_saved"] == 0 and "s from the server's" in result["skipped"][0]


def test_bars_get_the_index_price_of_their_minute_and_es_follows_the_live_basis(feed, monkeypatch) -> None:
    storage = feed.app.state.container.storage
    now = datetime.now(UTC)
    # Pretend these minutes are regular hours whatever the clock says when the test runs.
    monkeypatch.setattr("backend.api.routes.futures_live_feed.is_rth", lambda minute_et: True)
    bars = []
    for back in range(6, 0, -1):
        minute = _minute(now, back)
        storage.save_market_price(MarketPrice("SPX", minute + timedelta(seconds=58), Decimal("7400") + back, 0))
        bars.append({"symbol": "MES", "contract": "MES 12-26", "start": minute.isoformat(), "open": 7431, "high": 7432 + back, "low": 7430, "close": 7431 + back, "volume": 10})
    result = feed.post("/api/v1/feeds/futures-live", json={"bars": bars}, headers=KEY).json()
    assert result["bars_saved"] == 6
    saved = storage.get_futures_live_bars("ES", now - timedelta(minutes=10))
    assert {bar.index_price - (bar.close - 31) for bar in saved} == {Decimal(0)}
    if not is_front("MES 12-26", now.date()):
        pytest.skip("after the Dec 2026 roll the test contract is not front any more")
    # No anchor today: the live basis (+31) is the ES offset; the anchor offset is None.
    body = feed.get("/api/v1/futures/ES/live").json()
    assert body["offset"] == 31.0 and body["offset_source"] == "ninjatrader" and body["live_basis"] == 31.0


def test_a_minute_while_spx_is_replaced_by_spy_makes_no_basis(feed, monkeypatch) -> None:
    storage = feed.app.state.container.storage
    now = datetime.now(UTC)
    monkeypatch.setattr("backend.api.routes.futures_live_feed.is_rth", lambda minute_et: True)
    storage.save_index_price_fallback(IndexPriceFallback("SPX", "SPY", True, 10.03, "lag", now - timedelta(minutes=5), now))
    minute = _minute(now, 1)
    storage.save_market_price(MarketPrice("SPX", minute + timedelta(seconds=30), Decimal("7400"), 0))
    bar = {"symbol": "ES", "contract": "ES 12-26", "start": minute.isoformat(), "open": 7431, "high": 7431, "low": 7431, "close": 7431, "volume": 1}
    feed.post("/api/v1/feeds/futures-live", json={"bars": [bar]}, headers=KEY)
    [saved] = storage.get_futures_live_bars("ES", minute)
    assert saved.index_price is None


def test_the_real_last_price_is_only_for_the_owner(feed) -> None:
    now = datetime.now(UTC)
    quote = {"symbol": "ES", "contract": "ES 12-26", "last": 7445.25, "bid": 7445.0, "ask": 7445.25, "time": now.isoformat()}
    assert feed.post("/api/v1/feeds/futures-live", json={"quotes": [quote]}, headers=KEY).json()["quotes_saved"] == 1
    owner = feed.get("/api/v1/futures/ES/live").json()
    assert owner["quote"]["last"] == 7445.25 and owner["quote"]["contract"] == "ES 12-26"
    app.dependency_overrides[require_session] = lambda: SessionPayload(user_id=7, username="teammate1", is_admin=False)
    try:
        teammate = feed.get("/api/v1/futures/ES/live").json()
    finally:
        app.dependency_overrides[require_session] = lambda: SessionPayload(user_id=0, username="test-user", is_admin=True)
    assert teammate["quote"] is None
    assert feed.get("/api/v1/futures/CL/live").status_code == 404



def test_without_anchor_or_fresh_bars_the_last_known_basis_is_used(feed) -> None:
    # 2026-11-06: NQ had no 09:31 anchor; after the close 'Convexa Niveles' got 409 all evening. The last known NinjaTrader basis fills in.
    futures_proxy._last_basis_cache.clear()
    storage = feed.app.state.container.storage
    now = datetime.now(UTC)
    contract = "ES 12-26" if is_front("ES 12-26", now.date()) else "ES 03-27"
    old = (now - timedelta(hours=20)).replace(second=0, microsecond=0)
    storage.save_futures_live_bars([_bar(old - timedelta(minutes=i), "7431", "7400", contract=contract) for i in range(5)])
    body = feed.get("/api/v1/futures/ES/live").json()
    assert body["offset"] == 31.0 and body["offset_source"] == "ninjatrader_last" and body["live_basis"] is None
    futures_proxy._last_basis_cache.clear()
