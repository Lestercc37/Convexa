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


def test_flow_pressure_endpoint_reflects_a_classified_trade_once_exported() -> None:
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

        # StreamStateExporter (backend/core/stream_state_export.py) is
        # what actually persists this in production, on its own timer,
        # running only in backend/worker.py -- the process whose
        # WhaleAlertsEngine instance process_trade() actually feeds.
        # RefreshUnderlyingSnapshotUseCase.execute() (run by
        # /internal/trigger-calculation, this API process's own instance)
        # deliberately no longer does this itself (2026-09-22, the
        # scheduler/stream process split): see that use case's own
        # comment. Simulating one export tick directly here, same as
        # StreamStateExporter._export_once() does, keeps this a test of
        # the read endpoint against a real computed SymbolFlowPressure,
        # not a hardcoded one.
        flow_pressure = container.whale_alerts_engine.symbol_flow("SPY")
        assert flow_pressure is not None
        container.storage.save_symbol_flow_pressure(flow_pressure)

        response = client.get("/api/v1/flow/spy/pressure")

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
