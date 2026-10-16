"""Phase 1 of the multi-leg work (2026-10-08): the OPRA trade `condition` code is only CAPTURED -- parsed,
carried through the whale-alerts relay, tallied per bucket and stored on the alert -- and nothing is signed,
filtered or weighted by it. These tests pin that: the capture itself, and that switching it off (or leaving
the code out) changes no classification or amount."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.adapters.providers.thetadata.stream_parsing import parse_option_trade_message
from backend.adapters.storage.memory import InMemoryStorage
from backend.core.whale_alerts_relay import _decode_trade, _encode_trade
from backend.domain.entities import FlowEvent, FlowEventType, LatestQuote, Side
from backend.domain.use_cases import WhaleAlertsEngine, WhaleAlertType

SYMBOL = "IWM"
OCC = "IWM260220C00185000"
BASE_TIME = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
BUY_QUOTE = LatestQuote(bid=Decimal("0.01"), ask=Decimal("0.02"), as_of=BASE_TIME)


def _trade(period: int, premium: str, condition: int | None) -> FlowEvent:
    return FlowEvent(
        symbol=SYMBOL,
        occ_symbol=OCC,
        as_of=BASE_TIME + timedelta(minutes=period),
        event_type=FlowEventType.UNUSUAL,
        premium=Decimal(premium),
        size=1,
        aggressor_side=Side.UNKNOWN,
        condition=condition,
    )


def _message(trade: dict) -> dict:
    return {
        "contract": {"root": "SPXW", "expiration": 20261008, "strike": 7765000, "right": "P"},
        "trade": {"size": 3, "price": 1.5, **trade},
    }


class TestParsing:
    def test_the_condition_code_is_carried_on_the_flow_event(self) -> None:
        parsed = parse_option_trade_message(_message({"condition": 130}))
        assert parsed is not None and parsed.event is not None
        assert parsed.event.condition == 130
        assert parsed.size == 3  # volume handling is untouched

    def test_a_missing_or_malformed_condition_is_none(self) -> None:
        for trade in ({}, {"condition": None}, {"condition": "18"}, {"condition": 18.0}, {"condition": True}):
            parsed = parse_option_trade_message(_message(trade))
            assert parsed is not None and parsed.event is not None
            assert parsed.event.condition is None, trade


class TestRelay:
    def test_the_condition_survives_the_relay_encoding(self) -> None:
        event = _trade(0, "150", 125)
        decoded = _decode_trade(json.loads(_encode_trade(event)))
        assert decoded.condition == 125
        assert decoded == event

    def test_a_payload_from_a_sender_without_the_field_decodes_to_none(self) -> None:
        payload = json.loads(_encode_trade(_trade(0, "150", 18)))
        del payload["cond"]
        assert _decode_trade(payload).condition is None


def _run_alert(engine: WhaleAlertsEngine, conditions_by_trade: list[tuple[str, int | None]]):
    """Six quiet minutes, then one minute holding `conditions_by_trade`, then the trade that finalizes it."""
    for period in range(6):
        assert engine.process_trade(_trade(period, "100", 18), BUY_QUOTE) == ()
    for premium, condition in conditions_by_trade:
        engine.process_trade(_trade(6, premium, condition), BUY_QUOTE)
    return engine.process_trade(_trade(7, "100", 18), BUY_QUOTE)


class TestEngineCapture:
    def test_a_bucket_alert_splits_its_premium_by_condition_and_adds_up_to_the_amount(self) -> None:
        engine = WhaleAlertsEngine(InMemoryStorage())
        alerts = _run_alert(engine, [("30000", 130), ("10000", 18), ("5000", 130), ("20000", None)])

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.alert_type in (WhaleAlertType.UNUSUAL, WhaleAlertType.WHALE)
        assert alert.amount == Decimal(65000)
        assert alert.condition_premium == {"130": Decimal(35000), "18": Decimal(10000), "-1": Decimal(20000)}
        assert sum(alert.condition_premium.values()) == alert.amount

    def test_the_stored_alert_carries_the_split(self) -> None:
        storage = InMemoryStorage()
        engine = WhaleAlertsEngine(storage)
        _run_alert(engine, [("45000", 131)])
        saved = storage.get_recent_whale_alerts(SYMBOL)
        assert saved and saved[0].condition_premium == {"131": Decimal(45000)}

    def test_sustained_flow_sums_the_split_over_its_fifteen_minutes(self) -> None:
        engine = WhaleAlertsEngine(InMemoryStorage())
        collected = []
        # $40,000 a minute for 16 minutes: 15 finalized minutes = $600,000 >= the $500,000 sustained minimum.
        for period in range(16):
            collected += engine.process_trade(_trade(period, "25000", 130 if period % 2 else 18), BUY_QUOTE)
            collected += engine.process_trade(_trade(period, "15000", 18), BUY_QUOTE)
        collected += engine.process_trade(_trade(16, "100", 18), BUY_QUOTE)

        sustained = [a for a in collected if a.alert_type is WhaleAlertType.SUSTAINED_FLOW]
        assert len(sustained) == 1
        alert = sustained[0]
        assert alert.condition_premium is not None
        assert sum(alert.condition_premium.values()) == alert.amount
        assert set(alert.condition_premium) == {"130", "18"}

    def test_the_split_of_a_closed_bucket_is_not_wiped_by_the_next_bucket(self) -> None:
        engine = WhaleAlertsEngine(InMemoryStorage())
        alerts = _run_alert(engine, [("45000", 130)])
        engine.process_trade(_trade(7, "7000", 18), BUY_QUOTE)
        engine.process_trade(_trade(8, "100", 18), BUY_QUOTE)
        assert alerts[0].condition_premium == {"130": Decimal(45000)}

    def test_switching_the_capture_off_changes_nothing_but_the_split(self) -> None:
        trades = [("30000", 130), ("15000", 18)]
        on = _run_alert(WhaleAlertsEngine(InMemoryStorage()), trades)
        off = _run_alert(WhaleAlertsEngine(InMemoryStorage(), store_conditions=False), trades)

        assert len(on) == len(off) == 1
        assert off[0].condition_premium is None
        assert on[0].condition_premium is not None
        for field in ("alert_type", "amount", "estimated_buy_volume", "estimated_sell_volume", "quote_unavailable", "repeat_count"):
            assert getattr(on[0], field) == getattr(off[0], field), field

    def test_the_code_itself_never_changes_what_is_signed_or_summed(self) -> None:
        """A multi-leg print (130) is classified and counted exactly like an auto execution (18): phase 1 has no rule."""
        multi = _run_alert(WhaleAlertsEngine(InMemoryStorage()), [("45000", 130)])
        single = _run_alert(WhaleAlertsEngine(InMemoryStorage()), [("45000", 18)])
        assert (multi[0].amount, multi[0].estimated_buy_volume, multi[0].estimated_sell_volume) == (
            single[0].amount,
            single[0].estimated_buy_volume,
            single[0].estimated_sell_volume,
        )
