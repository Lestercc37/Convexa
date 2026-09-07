from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.domain.entities import FlowEvent, FlowEventType, LatestQuote, Side
from backend.main import app


def test_flow_pressure_endpoint_returns_not_found_before_any_trade() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/flow/spy/pressure")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_flow_pressure_endpoint_reflects_a_classified_trade_after_trigger_calculation() -> None:
    with TestClient(app) as client:
        container = app.state.container
        # symbol_flow() is fed only by process_trade() (the real trade
        # stream, see flow.py's own comment for why not process()/BVC
        # too) -- MockDataProvider never streams trades on its own, so
        # this seeds one directly, same as a real Worker's
        # StreamWhaleAlertsUseCase would.
        buy_leaning_quote = LatestQuote(
            bid=Decimal("0.01"), ask=Decimal("0.02"), as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
        )
        container.whale_alerts_engine.process_trade(
            FlowEvent(
                symbol="SPY",
                occ_symbol="SPY260220C00540000",
                as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
                event_type=FlowEventType.UNUSUAL,
                premium=Decimal("1000"),
                size=1,
                aggressor_side=Side.UNKNOWN,
            ),
            buy_leaning_quote,
        )

        # RefreshUnderlyingSnapshotUseCase.execute() (run here via the
        # manual trigger route) is what snapshots symbol_flow() to
        # storage -- see refresh_snapshot.py's own comment for why this
        # persistence step exists at all (the API process's own
        # WhaleAlertsEngine instance is otherwise never fed, post the
        # process split).
        trigger = client.post("/internal/trigger-calculation/spy")
        response = client.get("/api/v1/flow/spy/pressure")

    assert trigger.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert payload["net_call_premium"] == 1000
    assert payload["net_put_premium"] == 0
    assert payload["net_client_flow_pressure"] == 1000
    assert payload["rolling_window_minutes"] == 15
    # Explicit methodological caveat, per the request -- must say
    # "client"/aggressor, never claim confirmed dealer positioning.
    assert "dealer" in payload["methodology_note"].lower()
    assert "not" in payload["methodology_note"].lower()
