from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.api.routes import market_stream
from backend.domain.entities import MarketPrice
from backend.domain.use_cases import calculate_session_open, futures_proxy
from backend.main import app


def test_websocket_receives_a_published_tick_for_its_own_symbol() -> None:
    """Bypasses the real Postgres LISTEN connection (price_notification_listener
    is None under the sqlite test settings -- see container.py) and
    publishes directly through the same in-process hub the route reads
    from, same as PriceNotificationListener._on_notify would after a
    real NOTIFY. Proves the WebSocket <-> hub wiring itself, not the
    Postgres leg (covered separately by the integration test against a
    real database)."""
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/market/SPY") as websocket:
            payload = '{"symbol": "SPY", "price": "552.25", "as_of": "2026-09-05T14:30:00+00:00"}'
            app.state.container.price_notification_hub.publish("SPY", payload)

            received = websocket.receive_text()

    assert received == payload


def test_websocket_does_not_receive_a_tick_published_for_a_different_symbol() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/market/SPY") as websocket:
            app.state.container.price_notification_hub.publish(
                "QQQ", '{"symbol": "QQQ", "price": "470.10", "as_of": "2026-09-05T14:30:00+00:00"}'
            )
            app.state.container.price_notification_hub.publish(
                "SPY", '{"symbol": "SPY", "price": "552.25", "as_of": "2026-09-05T14:30:00+00:00"}'
            )

            received = websocket.receive_text()

    assert "SPY" in received
    assert "QQQ" not in received


def test_websocket_symbol_path_param_is_case_insensitive() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/market/spy") as websocket:
            payload = '{"symbol": "SPY", "price": "552.25", "as_of": "2026-09-05T14:30:00+00:00"}'
            app.state.container.price_notification_hub.publish("SPY", payload)

            received = websocket.receive_text()

    assert received == payload


def test_disconnecting_removes_the_subscriber_from_the_hub() -> None:
    # Captured only after TestClient(app) enters -- entering it runs the
    # real lifespan, which calls build_container() again and replaces
    # app.state.container wholesale (same object the route itself reads
    # per-request via websocket.app.state.container).
    with TestClient(app) as client:
        hub = app.state.container.price_notification_hub
        with client.websocket_connect("/api/v1/ws/market/SPY"):
            assert hub._subscribers.get("SPY")

        assert not hub._subscribers.get("SPY")


@pytest.fixture(autouse=True)
def _fresh_proxy_open_cache():
    futures_proxy._proxy_open_cache.clear()
    yield
    futures_proxy._proxy_open_cache.clear()


def _seed_index(storage, symbol: str, open_price: str, last_price: str) -> datetime:
    session_now = datetime.combine(datetime.now(UTC).date(), time(19, 0), UTC)
    storage.save_market_price(
        MarketPrice(
            symbol=symbol,
            as_of=session_now - timedelta(minutes=10),
            price=Decimal(open_price),
            volume=0,
        )
    )
    storage.save_market_price(
        MarketPrice(symbol=symbol, as_of=session_now, price=Decimal(last_price), volume=0)
    )
    return session_now


def _tick(symbol: str, price: str, as_of: datetime) -> str:
    return json.dumps({"symbol": symbol, "price": price, "as_of": as_of.isoformat()})


def test_es_socket_receives_spx_ticks_shifted_by_the_opening_price_offset() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        session_now = _seed_index(storage, "SPX", "5800", "5810")
        storage.set_future_price_anchor(
            "ES", calculate_session_open(session_now).date(), Decimal("5812.25")
        )
        with client.websocket_connect("/api/v1/ws/market/ES") as websocket:
            app.state.container.price_notification_hub.publish(
                "SPX", _tick("SPX", "5815.50", session_now)
            )

            received = json.loads(websocket.receive_text())

    # offset = 5812.25 (typed anchor) - 5800 (SPX's first price of the session) = 12.25
    assert received == {"symbol": "ES", "price": "5827.75", "as_of": session_now.isoformat()}


def test_nq_socket_is_fed_by_ndx() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        session_now = _seed_index(storage, "NDX", "20000", "20010")
        storage.set_future_price_anchor(
            "NQ", calculate_session_open(session_now).date(), Decimal("20100.00")
        )
        with client.websocket_connect("/api/v1/ws/market/nq") as websocket:
            app.state.container.price_notification_hub.publish(
                "NDX", _tick("NDX", "20020", session_now)
            )
            received = json.loads(websocket.receive_text())

    assert received["symbol"] == "NQ"
    assert received["price"] == "20120.00"


def test_future_socket_sends_nothing_until_the_opening_price_is_typed(monkeypatch) -> None:
    # The offset lookup answers "not typed yet" the first time and the number afterwards, so the
    # outcome does not depend on how the server task is scheduled against the test thread.
    answers = [None, Decimal("12.25")]

    async def offset_lookup(_storage, _future, _proxy):
        return answers.pop(0) if len(answers) > 1 else answers[0]

    monkeypatch.setattr(market_stream, "future_level_offset_async", offset_lookup)
    monkeypatch.setattr(market_stream, "OFFSET_MISSING_RETRY_SECONDS", 0.0)
    with TestClient(app) as client:
        session_now = datetime.combine(datetime.now(UTC).date(), time(19, 0), UTC)
        with client.websocket_connect("/api/v1/ws/market/ES") as websocket:
            hub = app.state.container.price_notification_hub
            hub.publish(
                "SPX", _tick("SPX", "5811", session_now)
            )  # not typed yet: dropped, never sent raw
            hub.publish("SPX", _tick("SPX", "5820", session_now))

            received = json.loads(websocket.receive_text())

    # The first message that arrives is the second tick, already in ES points.
    assert received["symbol"] == "ES"
    assert received["price"] == "5832.25"


def test_future_socket_subscribes_to_the_index_and_unsubscribes_on_disconnect() -> None:
    with TestClient(app) as client:
        hub = app.state.container.price_notification_hub
        with client.websocket_connect("/api/v1/ws/market/ES"):
            assert hub._subscribers.get("SPX")
            assert not hub._subscribers.get("ES")

        assert not hub._subscribers.get("SPX")


def test_shift_tick_ignores_a_malformed_payload() -> None:
    assert market_stream._shift_tick("not json", "ES", Decimal(1)) is None
    assert market_stream._shift_tick('{"symbol": "SPX"}', "ES", Decimal(1)) is None
    assert market_stream._shift_tick('{"symbol": "SPX", "price": "x"}', "ES", Decimal(1)) is None
