from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.providers.mock import MockDataProvider
from backend.domain.entities import OptionChain
from backend.domain.use_cases import WhaleAlert, WhaleAlertsEngine, WhaleAlertType
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

    cumulative += 450
    assert engine.process(_chain(base, cumulative, 6)) == ()
    # A 1-minute bucket only finalizes once a reading from the *next*
    # minute arrives — this call (no further delta) closes period 6's
    # bucket and is what actually gets classified.
    alerts = engine.process(_chain(base, cumulative, 7))

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

    cumulative += 1600
    engine.process(_chain(base, cumulative, 6))
    alerts = engine.process(_chain(base, cumulative, 7))

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

    cumulative += 300
    assert engine.process(_chain(base, cumulative, 6)) == ()
    assert engine.process(_chain(base, cumulative, 7)) == ()
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
        cumulative += 450
        engine.process(_chain(base, cumulative, 6))
        engine.process(_chain(base, cumulative, 7))

        response = client.get("/api/v1/alerts/spy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert payload["alerts"][0]["type"] == "UNUSUAL"
    assert payload["alerts"][0]["amount"] == 45000.0


def test_sustained_flow_fires_once_and_whale_still_classifies_independently() -> None:
    # Hand-verified: a steady $40,000/min flow (delta=400, last=$1.00) for
    # 15 straight minutes sums to $560,000 (the first finalized minute is
    # always $0 — no delta exists until a second reading arrives — so it's
    # 1x$0 + 14x$40,000), crossing sustained_flow_min ($500,000) on the
    # 15th finalized minute. $40,000/min never crosses Unusual on its own
    # (average converges to $40,000 too, and 40,000 is not > 40,000*3).
    engine = WhaleAlertsEngine()
    base = MockDataProvider().get_option_chain("IWM")
    cumulative = 100
    engine.process(_chain(base, cumulative, 0))

    for period in range(1, 15):
        cumulative += 400
        assert engine.process(_chain(base, cumulative, period)) == ()

    cumulative += 400
    # Period 15's own call closes period 14's bucket — the 15th push into
    # the 15-slot window — so the alert fires on this same call.
    sustained_alerts = engine.process(_chain(base, cumulative, 15))

    assert len(sustained_alerts) == 1
    assert sustained_alerts[0].alert_type is WhaleAlertType.SUSTAINED_FLOW
    assert sustained_alerts[0].amount == Decimal("560000.00")

    # Flow stays at the same elevated level — the sum stays above
    # threshold, but the alert does not repeat every minute (one-shot,
    # same "already_alerted" spirit specified for this new type).
    cumulative += 400
    assert engine.process(_chain(base, cumulative, 16)) == ()

    # A genuine one-off spike arrives on top of the steady flow — Whale
    # classification still fires normally, proving Sustained Flow's
    # bookkeeping doesn't interfere with the pre-existing mechanism, and
    # no duplicate Sustained Flow alert fires (still suppressed).
    cumulative += 5000
    engine.process(_chain(base, cumulative, 17))
    whale_alerts = engine.process(_chain(base, cumulative, 18))

    assert len(whale_alerts) == 1
    assert whale_alerts[0].alert_type is WhaleAlertType.WHALE
    assert whale_alerts[0].amount == Decimal("500000.00")


def test_sustained_flow_resets_and_refires_after_dropping_below_threshold() -> None:
    engine = WhaleAlertsEngine()
    base = MockDataProvider().get_option_chain("IWM")
    cumulative = 100
    engine.process(_chain(base, cumulative, 0))

    def run(period_start: int, count: int, delta: int) -> list[WhaleAlert]:
        nonlocal cumulative
        collected: list[WhaleAlert] = []
        for offset in range(count):
            cumulative += delta
            collected.extend(engine.process(_chain(base, cumulative, period_start + offset)))
        return collected

    def sustained(alerts: list[WhaleAlert]) -> list[WhaleAlert]:
        return [a for a in alerts if a.alert_type is WhaleAlertType.SUSTAINED_FLOW]

    # Phase 1: steady $40,000/min for 16 minutes — Sustained Flow fires
    # exactly once (period 15 closes the 15-slot window).
    phase1 = run(1, 16, 400)
    assert len(sustained(phase1)) == 1

    # Phase 2: flow drops to zero for long enough (more than the 15-slot
    # window) to fully flush the accumulated sum back under
    # sustained_flow_min, resetting the one-shot flag.
    phase2 = run(17, 20, 0)
    assert sustained(phase2) == []

    # Phase 3: flow ramps back up to $40,000/min — the window refills and
    # Sustained Flow fires a second, independent time.
    phase3 = run(37, 15, 400)
    assert len(sustained(phase3)) == 1
