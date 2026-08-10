from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.providers.mock import MockDataProvider
from backend.domain.entities import OptionChain
from backend.domain.use_cases import WhaleAlertsEngine, WhaleAlertType
from backend.main import app


def _chain(base: OptionChain, volume: int, period: int, last: str = "1.00") -> OptionChain:
    contract = replace(base.contracts[0], volume=volume, last=Decimal(last))
    return replace(
        base,
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
        + timedelta(minutes=period),
        contracts=(contract,),
    )


def test_engine_emits_unusual_after_five_previous_periods() -> None:
    engine = WhaleAlertsEngine()
    base = MockDataProvider().get_option_chain("IWM")

    cumulative = 100
    engine.process(_chain(base, cumulative, 0))
    for period in range(1, 6):
        cumulative += 100
        assert engine.process(_chain(base, cumulative, period)) == ()

    alerts = engine.process(_chain(base, cumulative + 450, 6))

    assert len(alerts) == 1
    assert alerts[0].alert_type is WhaleAlertType.UNUSUAL
    assert alerts[0].amount == Decimal("45000.00")


def test_engine_emits_whale_and_prioritizes_it_over_unusual() -> None:
    engine = WhaleAlertsEngine()
    base = MockDataProvider().get_option_chain("IWM")

    cumulative = 100
    engine.process(_chain(base, cumulative, 0))
    for period in range(1, 6):
        cumulative += 200
        engine.process(_chain(base, cumulative, period))

    alerts = engine.process(_chain(base, cumulative + 1600, 6))

    assert len(alerts) == 1
    assert alerts[0].alert_type is WhaleAlertType.WHALE
    assert alerts[0].amount == Decimal("160000.00")


def test_engine_does_not_alert_below_multiplier_or_dollar_threshold() -> None:
    engine = WhaleAlertsEngine()
    base = MockDataProvider().get_option_chain("IWM")

    cumulative = 100
    engine.process(_chain(base, cumulative, 0))
    for period in range(1, 6):
        cumulative += 100
        engine.process(_chain(base, cumulative, period))

    assert engine.process(_chain(base, cumulative + 300, 6)) == ()
    assert engine.recent_alerts("IWM") == ()


def test_alerts_endpoint_is_read_only_and_returns_recent_alerts() -> None:
    with TestClient(app) as client:
        engine = app.state.container.whale_alerts_engine
        base = MockDataProvider().get_option_chain("SPY")
        cumulative = 100
        engine.process(_chain(base, cumulative, 0))
        for period in range(1, 6):
            cumulative += 100
            engine.process(_chain(base, cumulative, period))
        engine.process(_chain(base, cumulative + 450, 6))

        response = client.get("/api/v1/alerts/spy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert payload["alerts"][0]["type"] == "UNUSUAL"
    assert payload["alerts"][0]["amount"] == 45000.0
