"""Alerts from the open (Lester 2026-10-22): with partial windows a contract's alerts start as soon as its own history allows instead of after 5 / 15 finalized minutes.
UNUSUAL / WHALE from 2 finalized minutes (average of the minutes it has, at most 5); SUSTAINED_FLOW from 3 minutes against sustained_flow_min x minutes / 15. The engine default stays the legacy full-window rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.adapters.storage.memory import InMemoryStorage
from backend.core.container import build_whale_alerts_engine
from backend.domain.entities import FlowEvent, FlowEventType, LatestQuote, Side
from backend.domain.use_cases import WhaleAlertsEngine, WhaleAlertType

SYMBOL = "IWM"
OCC = "IWM260220C00185000"
BASE_TIME = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
QUOTE = LatestQuote(bid=Decimal("0.01"), ask=Decimal("0.02"), as_of=BASE_TIME)


def _trade(minute: int, premium: str) -> FlowEvent:
    return FlowEvent(
        symbol=SYMBOL,
        occ_symbol=OCC,
        as_of=BASE_TIME + timedelta(minutes=minute),
        event_type=FlowEventType.UNUSUAL,
        premium=Decimal(premium),
        size=1,
        aggressor_side=Side.UNKNOWN,
    )


def _feed(engine: WhaleAlertsEngine, premiums: list[str]):
    """One trade per minute with the given premiums, then a closing trade that finalizes the last one. Returns (alert, minute index of the trade that emitted it)."""
    out = []
    for minute, premium in enumerate(premiums):
        for alert in engine.process_trade(_trade(minute, premium), QUOTE):
            out.append((alert, minute))
    for alert in engine.process_trade(_trade(len(premiums), "100"), QUOTE):
        out.append((alert, len(premiums)))
    return out


def test_the_engine_default_is_still_the_legacy_full_window_rule() -> None:
    # $40,000 for 4 minutes would fire sustained at minute 3 with partial windows; legacy needs 15 minutes.
    engine = WhaleAlertsEngine(InMemoryStorage())
    assert _feed(engine, ["40000"] * 4) == []


def test_sustained_flow_fires_from_three_minutes_with_a_prorated_minimum() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage(), partial_windows=True)
    # sustained_flow_min $500,000 -> 3 minutes need $100,000: $40,000 a minute reaches $120,000 on the 3rd finalized minute.
    alerts = _feed(engine, ["40000"] * 4)
    sustained = [(a, m) for a, m in alerts if a.alert_type is WhaleAlertType.SUSTAINED_FLOW]
    assert len(sustained) == 1
    alert, emitted_at = sustained[0]
    assert emitted_at == 3  # the trade of minute 3 finalizes minute 2, the third bucket
    assert alert.amount == Decimal(120000)
    assert alert.trade_minutes == 3
    assert alert.trade_start == BASE_TIME


def test_below_the_prorated_minimum_nothing_fires() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage(), partial_windows=True)
    # $30,000 a minute: 3 minutes = $90,000 < $100,000; 4 minutes = $120,000 < $133,333; stays below 500,000 x k / 15 at every k.
    assert [a for a, _ in _feed(engine, ["30000"] * 14) if a.alert_type is WhaleAlertType.SUSTAINED_FLOW] == []


def _sustained(alerts):
    return [(a, m) for a, m in alerts if a.alert_type is WhaleAlertType.SUSTAINED_FLOW]


def test_at_fifteen_minutes_the_rule_is_exactly_the_old_one() -> None:
    legacy = WhaleAlertsEngine(InMemoryStorage())
    partial = WhaleAlertsEngine(InMemoryStorage(), partial_windows=True)
    flow = ["40000"] * 16
    legacy_alerts = [a for a, _ in _sustained(_feed(legacy, flow))]
    assert len(legacy_alerts) == 1 and legacy_alerts[0].trade_minutes == 15
    # the same flow also satisfies the partial rule earlier (3 minutes), and the classic 15-minute alert still follows, identical to legacy's
    partial_alerts = [a for a, _ in _sustained(_feed(partial, flow))]
    assert [a.trade_minutes for a in partial_alerts] == [3, 15]
    assert partial_alerts[1].amount == legacy_alerts[0].amount == Decimal(600000)
    assert partial_alerts[1].trade_start == legacy_alerts[0].trade_start


def test_the_full_window_alert_comes_once_per_episode_when_the_window_fills() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage(), partial_windows=True)
    # 25 minutes above the full minimum: early alert at the 3rd bucket, the 15-minute one when the window fills, nothing after (window stays full)
    alerts = _sustained(_feed(engine, ["40000"] * 25))
    assert [(a.trade_minutes, m) for a, m in alerts] == [(3, 3), (15, 15)]


def test_no_full_window_alert_when_the_window_fills_below_the_full_minimum() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage(), partial_windows=True)
    # $120,000 in the first 3 minutes (early alert: >= $100,000), then $10,000 a minute: 3 x 40,000 + 12 x 10,000 = $240,000 < $500,000 at 15 minutes.
    # The total falls below the prorated minimum right after (4 minutes: $130,000 < $133,333), which ends the episode, and never climbs back.
    alerts = _sustained(_feed(engine, ["40000"] * 3 + ["10000"] * 12))
    assert [a.trade_minutes for a, _ in alerts] == [3]


def test_a_new_episode_after_the_reset_gets_its_own_early_and_full_alerts() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage(), partial_windows=True)
    # episode 1: 16 strong minutes (3 and 15); then 15 silent-ish minutes drain the window below the minimum (reset); episode 2: the window is
    # full again (15 buckets) when it crosses, so its first alert is already the 15-minute one and is not repeated.
    flow = ["40000"] * 16 + ["100"] * 15 + ["40000"] * 15
    alerts = _sustained(_feed(engine, flow))
    assert [a.trade_minutes for a, _ in alerts] == [3, 15, 15]


def test_the_switch_turns_the_full_window_alert_off() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage(), partial_windows=True, full_window_sustained=False)
    assert [a.trade_minutes for a, _ in _sustained(_feed(engine, ["40000"] * 16))] == [3]
    # and without partial windows it changes nothing: one 15-minute alert, as before
    legacy = WhaleAlertsEngine(InMemoryStorage(), full_window_sustained=False)
    assert [a.trade_minutes for a, _ in _sustained(_feed(legacy, ["40000"] * 16))] == [15]


def test_a_whale_minute_is_classified_from_two_previous_minutes() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage(), partial_windows=True)
    # IWM thresholds (defaults): whale_min $150,000 and > 6x the average. Two quiet minutes of $10,000 then $200,000 (20x) then the closing trade.
    alerts = _feed(engine, ["10000", "10000", "200000"])
    whales = [a for a, _ in alerts if a.alert_type is WhaleAlertType.WHALE]
    assert len(whales) == 1 and whales[0].amount == Decimal(200000)
    # legacy needs five previous minutes: the same flow is silent
    assert [a for a, _ in _feed(WhaleAlertsEngine(InMemoryStorage()), ["10000", "10000", "200000"]) if a.alert_type is WhaleAlertType.WHALE] == []


def test_one_previous_minute_is_not_enough_to_call_anything_unusual() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage(), partial_windows=True)
    assert [a for a, _ in _feed(engine, ["10000", "200000"]) if a.alert_type in (WhaleAlertType.WHALE, WhaleAlertType.UNUSUAL)] == []


def test_the_average_uses_only_the_minutes_that_exist() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage(), partial_windows=True)
    # previous minutes $50,000 and $50,000 (average $50,000); $160,000 is > 3x the average and >= $40,000 unusual_min but < 6x: UNUSUAL, not WHALE.
    alerts = _feed(engine, ["50000", "50000", "160000"])
    kinds = [a.alert_type for a, _ in alerts if a.alert_type is not WhaleAlertType.SUSTAINED_FLOW]
    assert kinds == [WhaleAlertType.UNUSUAL]


def test_production_builder_turns_partial_windows_on_and_the_switch_turns_them_off() -> None:
    assert build_whale_alerts_engine(InMemoryStorage())._partial_windows is True
    assert build_whale_alerts_engine(InMemoryStorage(), partial_windows=False)._partial_windows is False
    assert build_whale_alerts_engine(InMemoryStorage())._full_window_sustained is True
    assert build_whale_alerts_engine(InMemoryStorage(), full_window_sustained=False)._full_window_sustained is False
