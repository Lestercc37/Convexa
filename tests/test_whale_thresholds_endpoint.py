from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.providers.mock import MockDataProvider
from backend.domain.entities import OptionChain
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.main import app


def _chain(base: OptionChain, volume: int, period: int) -> OptionChain:
    return replace(
        base,
        as_of=datetime(2026, 8, 6, 14, 30, tzinfo=UTC) + timedelta(minutes=period),
        contracts=(replace(base.contracts[0], volume=volume, last=Decimal("1.00")),),
    )


def test_list_endpoint_returns_all_active_symbols_with_defaults() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/whale-thresholds")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    symbols = {item["symbol"] for item in payload["thresholds"]}
    assert symbols == {underlying.symbol for underlying in ACTIVE_UNDERLYINGS}
    spy = next(item for item in payload["thresholds"] if item["symbol"] == "SPY")
    assert spy == {
        "symbol": "SPY",
        "unusual_min": 40000,
        "whale_min": 150000,
        "unusual_multiplier": 3.0,
        "whale_multiplier": 6.0,
        "sustained_flow_min": 500000,
    }


def test_patch_endpoint_updates_the_persisted_row() -> None:
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/whale-thresholds/spy",
            json={
                "unusual_min": 20000,
                "whale_min": 100000,
                "unusual_multiplier": 2.5,
                "whale_multiplier": 5.5,
                "sustained_flow_min": 400000,
            },
        )
        assert response.status_code == 200
        assert response.json() == {
            "symbol": "SPY",
            "unusual_min": 20000,
            "whale_min": 100000,
            "unusual_multiplier": 2.5,
            "whale_multiplier": 5.5,
            "sustained_flow_min": 400000,
        }

        listed = client.get("/api/v1/whale-thresholds").json()

    updated = next(item for item in listed["thresholds"] if item["symbol"] == "SPY")
    assert updated["unusual_min"] == 20000
    assert updated["sustained_flow_min"] == 400000


def test_patch_endpoint_rejects_non_positive_values() -> None:
    with TestClient(app) as client:
        zero = client.patch(
            "/api/v1/whale-thresholds/spy",
            json={
                "unusual_min": 0,
                "whale_min": 100000,
                "unusual_multiplier": 2.5,
                "whale_multiplier": 5.5,
                "sustained_flow_min": 400000,
            },
        )
        negative = client.patch(
            "/api/v1/whale-thresholds/spy",
            json={
                "unusual_min": 20000,
                "whale_min": -100000,
                "unusual_multiplier": 2.5,
                "whale_multiplier": 5.5,
                "sustained_flow_min": 400000,
            },
        )

    assert zero.status_code == 422
    assert negative.status_code == 422


def test_patch_endpoint_rejects_missing_fields() -> None:
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/whale-thresholds/spy",
            json={"unusual_min": 20000, "whale_min": 100000},
        )

    assert response.status_code == 422


def test_patch_endpoint_returns_not_found_for_unknown_symbol() -> None:
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/whale-thresholds/notarealsymbol",
            json={
                "unusual_min": 20000,
                "whale_min": 100000,
                "unusual_multiplier": 2.5,
                "whale_multiplier": 5.5,
                "sustained_flow_min": 400000,
            },
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_patch_takes_effect_on_the_engines_next_process_call() -> None:
    # End-to-end proof Piece 1 + Piece 2 actually work together: an edit
    # through the real HTTP endpoint changes real classification on the
    # live container's long-lived engine, no restart.
    with TestClient(app) as client:
        base = MockDataProvider().get_option_chain("QQQ")
        engine = app.state.container.whale_alerts_engine

        cumulative = 100
        engine.process(_chain(base, cumulative, 0))
        for period in range(1, 6):
            cumulative += 100
            engine.process(_chain(base, cumulative, period))
        cumulative += 450
        engine.process(_chain(base, cumulative, 6))
        assert engine.process(_chain(base, cumulative, 7))[0].alert_type.value == "UNUSUAL"

        patch = client.patch(
            "/api/v1/whale-thresholds/qqq",
            json={
                "unusual_min": 100000,
                "whale_min": 200000,
                "unusual_multiplier": 3.0,
                "whale_multiplier": 6.0,
                "sustained_flow_min": 500000,
            },
        )
        assert patch.status_code == 200

        for period in range(8, 14):
            cumulative += 100
            engine.process(_chain(base, cumulative, period))
        cumulative += 450
        engine.process(_chain(base, cumulative, 14))
        assert engine.process(_chain(base, cumulative, 15)) == ()
