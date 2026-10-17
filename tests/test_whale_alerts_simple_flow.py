"""Trial second label of a whale alert (2026-10-16, heatmap design 12.14): WhaleAlert.simple_flow = the alert's buy/sell premium
counting ONLY trades whose OPRA condition is 18, 95, 125 or 126. It is capture only: the main figures (estimated_buy_volume /
estimated_sell_volume, amount, alert type) must come out exactly as before, with or without it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import FlowEvent, FlowEventType, LatestQuote, Side
from backend.domain.use_cases import WhaleAlertsEngine, WhaleAlertType

SYMBOL = "IWM"
OCC = "IWM260220C00185000"
BASE_TIME = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
# A trade price is premium / (size x 100): $300 for the $30,000 trades below. Above the midpoint (0.015) = BUY.
BUY_QUOTE = LatestQuote(bid=Decimal("0.01"), ask=Decimal("0.02"), as_of=BASE_TIME)
# Midpoint $450: a $300 trade prints below it = SELL.
SELL_QUOTE = LatestQuote(bid=Decimal("400"), ask=Decimal("500"), as_of=BASE_TIME)


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


def _run_alert(engine: WhaleAlertsEngine, trades: list[tuple[str, int | None, LatestQuote | None]]):
    """Six quiet minutes, one minute holding `trades`, then the trade that finalizes it."""
    for period in range(6):
        assert engine.process_trade(_trade(period, "100", 18), BUY_QUOTE) == ()
    for premium, condition, quote in trades:
        engine.process_trade(_trade(6, premium, condition), quote)
    return engine.process_trade(_trade(7, "100", 18), BUY_QUOTE)


def test_only_trusted_conditions_count_and_the_main_figures_are_untouched() -> None:
    alerts = _run_alert(
        WhaleAlertsEngine(InMemoryStorage()),
        [("30000", 130, BUY_QUOTE), ("10000", 18, BUY_QUOTE), ("5000", 129, BUY_QUOTE), ("20000", None, BUY_QUOTE), ("15000", 125, BUY_QUOTE)],
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.alert_type in (WhaleAlertType.UNUSUAL, WhaleAlertType.WHALE)
    assert alert.amount == Decimal(80000)
    # Main label: every trade signed (all BUY here).
    assert alert.estimated_buy_volume == Decimal(80000) and alert.estimated_sell_volume == Decimal(0)
    # Second label: only 18 and 125 (the 130, 129 and condition-less trades stay out).
    assert alert.simple_flow == {"buy": Decimal(25000), "sell": Decimal(0)}


def test_signed_sells_are_counted_on_the_sell_side() -> None:
    alerts = _run_alert(
        WhaleAlertsEngine(InMemoryStorage()),
        [("30000", 18, SELL_QUOTE), ("10000", 126, BUY_QUOTE), ("40000", 130, SELL_QUOTE)],
    )
    alert = alerts[0]
    assert alert.simple_flow == {"buy": Decimal(10000), "sell": Decimal(30000)}
    assert alert.estimated_sell_volume == Decimal(70000)  # the main label also counts the multi-leg sell


def test_a_trusted_trade_with_no_quote_splits_half_and_half_like_the_main_figures() -> None:
    alerts = _run_alert(WhaleAlertsEngine(InMemoryStorage()), [("40000", 18, None), ("20000", 130, None)])
    alert = alerts[0]
    assert alert.simple_flow == {"buy": Decimal(20000), "sell": Decimal(20000)}


def test_a_bucket_with_no_trusted_trade_has_a_zero_split_not_none() -> None:
    alerts = _run_alert(WhaleAlertsEngine(InMemoryStorage()), [("45000", 130, BUY_QUOTE)])
    assert alerts[0].simple_flow == {"buy": Decimal(0), "sell": Decimal(0)}


def test_the_stored_alert_carries_the_split() -> None:
    storage = InMemoryStorage()
    _run_alert(WhaleAlertsEngine(storage), [("45000", 18, BUY_QUOTE)])
    saved = storage.get_recent_whale_alerts(SYMBOL)
    assert saved and saved[0].simple_flow == {"buy": Decimal(45000), "sell": Decimal(0)}


def test_sustained_flow_sums_the_split_over_its_fifteen_minutes() -> None:
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
    assert alert.amount == Decimal(600000)
    # 15 finalized minutes (the first 15): the 15,000 trade every minute, plus the 25,000 one on the even minutes (18) only.
    even_minutes = len([p for p in range(15) if p % 2 == 0])
    assert alert.simple_flow == {"buy": Decimal(15 * 15000 + even_minutes * 25000), "sell": Decimal(0)}


def test_the_split_of_a_closed_bucket_is_not_wiped_by_the_next_bucket() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage())
    alerts = _run_alert(engine, [("45000", 18, BUY_QUOTE)])
    engine.process_trade(_trade(7, "7000", 18), BUY_QUOTE)
    engine.process_trade(_trade(8, "100", 18), BUY_QUOTE)
    assert alerts[0].simple_flow == {"buy": Decimal(45000), "sell": Decimal(0)}


def test_switching_the_capture_off_removes_the_split_and_changes_nothing_else() -> None:
    trades = [("30000", 130, BUY_QUOTE), ("15000", 18, BUY_QUOTE)]
    on = _run_alert(WhaleAlertsEngine(InMemoryStorage()), trades)
    off = _run_alert(WhaleAlertsEngine(InMemoryStorage(), store_conditions=False), trades)

    assert len(on) == len(off) == 1
    assert off[0].simple_flow is None and on[0].simple_flow is not None
    for field in ("alert_type", "amount", "estimated_buy_volume", "estimated_sell_volume", "quote_unavailable", "repeat_count"):
        assert getattr(on[0], field) == getattr(off[0], field), field
