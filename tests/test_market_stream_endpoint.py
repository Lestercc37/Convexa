from __future__ import annotations

from fastapi.testclient import TestClient

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
