"""What the heatmap's alert cubes need from each alert (migration 0046): the contracts traded in the alert's window and the contract's |delta|
when it fired, plus the fields the alerts endpoint now sends (condition split and trial label)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import FlowEvent, FlowEventType, LatestQuote, Side
from backend.domain.use_cases import WhaleAlertsEngine, WhaleAlertType
from backend.domain.use_cases.flow import WhaleAlert
from backend.main import app

SYMBOL = "IWM"
OCC = "IWM260220C00185000"
BASE_TIME = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
BUY_QUOTE = LatestQuote(bid=Decimal("0.01"), ask=Decimal("0.02"), as_of=BASE_TIME)


class _DeltaStorage(InMemoryStorage):
    def __init__(self, delta: Decimal | None) -> None:
        super().__init__()
        self._delta = delta

    def get_latest_option_delta(self, occ_symbol: str) -> Decimal | None:
        return self._delta


def _trade(period: int, premium: str, size: int, condition: int | None = 18) -> FlowEvent:
    return FlowEvent(
        symbol=SYMBOL,
        occ_symbol=OCC,
        as_of=BASE_TIME + timedelta(minutes=period),
        event_type=FlowEventType.UNUSUAL,
        premium=Decimal(premium),
        size=size,
        aggressor_side=Side.UNKNOWN,
        condition=condition,
    )


def test_a_minute_alert_carries_the_contracts_of_its_bucket_and_the_absolute_delta() -> None:
    engine = WhaleAlertsEngine(_DeltaStorage(Decimal("-0.42")))  # a put's delta is negative: the cube keeps the magnitude
    for period in range(6):
        engine.process_trade(_trade(period, "100", 1), BUY_QUOTE)
    for premium, size in (("30000", 12), ("20000", 8), ("15000", 5)):
        engine.process_trade(_trade(6, premium, size, condition=130), BUY_QUOTE)  # multi-leg trades count as contracts too
    alerts = engine.process_trade(_trade(7, "100", 1), BUY_QUOTE)

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.alert_type in (WhaleAlertType.UNUSUAL, WhaleAlertType.WHALE)
    assert alert.contracts == 25
    assert alert.delta == Decimal("0.42")
    # The trades happened in minute 6; the alert was issued by the trade of minute 7 that closed the bucket.
    assert alert.trade_start == BASE_TIME + timedelta(minutes=6)
    assert alert.trade_minutes == 1
    assert alert.as_of == BASE_TIME + timedelta(minutes=7)


def test_a_sustained_alert_sums_the_contracts_of_its_fifteen_minutes() -> None:
    engine = WhaleAlertsEngine(_DeltaStorage(Decimal("0.5")))
    collected = []
    # $40,000 and 10 + 5 contracts a minute; 15 finalized minutes cover $600,000 >= the $500,000 sustained minimum.
    for period in range(16):
        collected += engine.process_trade(_trade(period, "25000", 10), BUY_QUOTE)
        collected += engine.process_trade(_trade(period, "15000", 5), BUY_QUOTE)
    collected += engine.process_trade(_trade(16, "100", 1), BUY_QUOTE)

    sustained = [a for a in collected if a.alert_type is WhaleAlertType.SUSTAINED_FLOW]
    assert len(sustained) == 1
    assert sustained[0].contracts == 15 * 15
    assert sustained[0].delta == Decimal("0.5")
    assert sustained[0].trade_start == BASE_TIME  # the first of its 15 minutes
    assert sustained[0].trade_minutes == 15


def test_a_missing_or_zero_delta_is_none_not_zero() -> None:
    for stored in (None, Decimal("0")):
        engine = WhaleAlertsEngine(_DeltaStorage(stored))
        for period in range(6):
            engine.process_trade(_trade(period, "100", 1), BUY_QUOTE)
        engine.process_trade(_trade(6, "45000", 9), BUY_QUOTE)
        alert = engine.process_trade(_trade(7, "100", 1), BUY_QUOTE)[0]
        assert alert.delta is None
        assert alert.contracts == 9


def test_the_contracts_of_a_closed_bucket_are_not_wiped_by_the_next_bucket() -> None:
    engine = WhaleAlertsEngine(_DeltaStorage(Decimal("0.5")))
    for period in range(6):
        engine.process_trade(_trade(period, "100", 1), BUY_QUOTE)
    engine.process_trade(_trade(6, "45000", 9), BUY_QUOTE)
    alert = engine.process_trade(_trade(7, "7000", 4), BUY_QUOTE)[0]
    engine.process_trade(_trade(8, "100", 1), BUY_QUOTE)
    assert alert.contracts == 9


def test_the_alerts_endpoint_sends_the_cube_fields_and_null_for_old_alerts() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        base = dict(
            symbol="SPX",
            alert_type=WhaleAlertType.UNUSUAL,
            amount=Decimal("65000"),
            estimated_buy_volume=Decimal("65000"),
            estimated_sell_volume=Decimal("0"),
        )
        storage.save_whale_alert(
            WhaleAlert(
                occ_symbol="SPXW261016C07720000",
                as_of=datetime(2026, 10, 16, 14, 52, 5, tzinfo=UTC),
                condition_premium={"130": Decimal("62400"), "18": Decimal("2600")},
                simple_flow={"buy": Decimal("0"), "sell": Decimal("2600")},
                delta=Decimal("0.84"),
                contracts=444,
                trade_start=datetime(2026, 10, 16, 14, 51, tzinfo=UTC),
                trade_minutes=1,
                **base,
            )
        )
        storage.save_whale_alert(
            WhaleAlert(occ_symbol="SPXW261016P07725000", as_of=datetime(2026, 10, 16, 14, 50, tzinfo=UTC), **base)
        )

        response = client.get("/api/v1/alerts/SPX")

    assert response.status_code == 200
    alerts = {alert["contract"]: alert for alert in response.json()["alerts"]}
    full = alerts["SPXW261016C07720000"]
    assert (full["delta"], full["contracts"]) == (0.84, 444)
    assert full["condition_premium"] == {"130": 62400, "18": 2600}
    assert full["simple_flow"] == {"buy": 0, "sell": 2600}
    assert full["timestamp"].startswith("2026-10-16T14:52:05")  # issued
    assert full["trade_start"].startswith("2026-10-16T14:51:00")  # traded
    assert full["trade_minutes"] == 1
    old = alerts["SPXW261016P07725000"]
    assert (old["delta"], old["contracts"], old["condition_premium"], old["simple_flow"]) == (None, None, None, None)
    assert (old["trade_start"], old["trade_minutes"]) == (None, None)
