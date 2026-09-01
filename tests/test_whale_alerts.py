from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.providers.mock import MockDataProvider
from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import (
    FlowEvent,
    FlowEventType,
    LatestQuote,
    OptionChain,
    Side,
)
from backend.domain.use_cases import (
    WhaleAlert,
    WhaleAlertsEngine,
    WhaleAlertType,
    calculate_bvc_split,
    calculate_price_volatility,
)
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
    engine = WhaleAlertsEngine(InMemoryStorage())
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
    engine = WhaleAlertsEngine(InMemoryStorage())
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


def test_bvc_split_on_a_real_alert_matches_the_pure_function_given_the_same_inputs() -> None:
    # Same shape as the WHALE test above, but with a genuinely varying
    # price (not the constant "1.00" _chain defaults to) so BVC computes a
    # real, non-neutral split — proof the engine's own rolling price
    # window and per-reading accumulation are wired correctly, not just
    # that the pure function in calculate_bvc.py is correct in isolation
    # (already covered by tests/test_bvc.py).
    engine = WhaleAlertsEngine(InMemoryStorage())
    base = MockDataProvider().get_option_chain("IWM")
    prices = ["1.00", "1.02", "0.99", "1.03", "1.00", "1.05", "1.10"]

    cumulative = 100
    engine.process(_chain(base, cumulative, 0, last=prices[0]))
    for period in range(1, 6):
        cumulative += 200
        engine.process(_chain(base, cumulative, period, last=prices[period]))

    cumulative += 1600
    # Closes period 5's bucket, starts period 6's with this one reading.
    engine.process(_chain(base, cumulative, 6, last=prices[6]))
    # Closes period 6's bucket — same price as period 6, so this reading
    # contributes nothing further to period 6's already-finalized bucket.
    alerts = engine.process(_chain(base, cumulative, 7, last=prices[6]))

    assert len(alerts) == 1
    assert alerts[0].alert_type is WhaleAlertType.WHALE

    # Independently reconstruct what period 6's single raw reading should
    # have produced: the rolling price-delta window built from periods
    # 1-6's own price changes (population stdev, same as the engine uses),
    # then BVC applied to period 6's own 1,600-contract volume delta.
    price_deltas = [
        Decimal(prices[i]) - Decimal(prices[i - 1]) for i in range(1, len(prices))
    ]
    sigma = calculate_price_volatility(price_deltas)
    expected_buy, expected_sell = calculate_bvc_split(
        price_deltas[-1], sigma, Decimal(1600)
    )

    assert alerts[0].estimated_buy_volume == expected_buy
    assert alerts[0].estimated_sell_volume == expected_sell
    assert expected_buy + expected_sell == Decimal(1600)
    # The final price change (period 5 -> period 6, +0.05) is positive and
    # not the largest swing in the window, so it should skew toward
    # buying without being an extreme, saturated split.
    assert expected_buy > expected_sell
    assert Decimal("800") < expected_buy < Decimal("1600")


def test_bvc_volatility_window_evicts_by_elapsed_time_not_reading_count() -> None:
    # Same price sequence and structure as the test above, but spaced far
    # enough apart in wall-clock time (20 minutes between readings, not 1)
    # that every price delta falls outside the rolling window
    # (_PRICE_VOLATILITY_WINDOW, 10 minutes) by the time the next reading
    # arrives. Under the old deque(maxlen=20) design this same 7-reading
    # sequence would never evict anything (7 << 20) and would produce the
    # exact same non-neutral split as the test above, regardless of how
    # much real time passed between readings — proof the window is now
    # anchored to elapsed time, not reading count.
    engine = WhaleAlertsEngine(InMemoryStorage())
    base = MockDataProvider().get_option_chain("IWM")
    prices = ["1.00", "1.02", "0.99", "1.03", "1.00", "1.05", "1.10"]
    period_minutes = 20  # > _PRICE_VOLATILITY_WINDOW (10 minutes)

    cumulative = 100
    engine.process(_chain(base, cumulative, 0, last=prices[0]))
    for period in range(1, 6):
        cumulative += 200
        engine.process(_chain(base, cumulative, period * period_minutes, last=prices[period]))

    cumulative += 1600
    engine.process(_chain(base, cumulative, 6 * period_minutes, last=prices[6]))
    alerts = engine.process(_chain(base, cumulative, 7 * period_minutes, last=prices[6]))

    assert len(alerts) == 1
    assert alerts[0].alert_type is WhaleAlertType.WHALE
    # Each reading's own delta is the only one left in its window (the
    # prior reading's delta is always >10 minutes old by then), so sigma
    # is a single-point population stdev — exactly 0 — every time. BVC's
    # documented sigma==0 fallback is a neutral 50/50 split.
    assert alerts[0].estimated_buy_volume == alerts[0].estimated_sell_volume
    assert alerts[0].estimated_buy_volume == Decimal("800")


def test_engine_does_not_alert_below_multiplier_or_dollar_threshold() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage())
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


def test_threshold_edits_take_effect_on_the_next_process_call_without_rebuilding_the_engine() -> None:
    # The whole point of Piece 1: an edit written to storage after the
    # engine is constructed must change classification on the very next
    # process() call — no restart, no rebuilding the engine (which would
    # also wipe _states/_alerts).
    storage = InMemoryStorage()
    engine = WhaleAlertsEngine(storage)
    base = MockDataProvider().get_option_chain("IWM")

    cumulative = 100
    engine.process(_chain(base, cumulative, 0))
    for period in range(1, 6):
        cumulative += 100
        engine.process(_chain(base, cumulative, period))

    # A $45,000 finalized minute at a $10,000 average is UNUSUAL under the
    # default $40,000 threshold (same shape as the periods-5 test above).
    cumulative += 450
    engine.process(_chain(base, cumulative, 6))
    assert engine.process(_chain(base, cumulative, 7))[0].alert_type is WhaleAlertType.UNUSUAL

    # Raise unusual_min above $45,000 directly in storage — no engine
    # rebuild — then feed the exact same shape through again.
    raised = replace(
        storage.get_whale_thresholds()["IWM"],
        unusual_min=Decimal("100000"),
    )
    storage.save_whale_threshold(raised)

    for period in range(8, 14):
        cumulative += 100
        engine.process(_chain(base, cumulative, period))
    cumulative += 450
    engine.process(_chain(base, cumulative, 14))
    assert engine.process(_chain(base, cumulative, 15)) == ()


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
    # `_chain` uses a constant price ("1.00") throughout — zero price
    # variance means sigma stays 0, so BVC falls back to a neutral 50/50
    # split of the finalized minute's 450-contract volume delta.
    assert payload["alerts"][0]["estimated_buy_volume"] == 225.0
    assert payload["alerts"][0]["estimated_sell_volume"] == 225.0


def test_sustained_flow_fires_once_and_whale_still_classifies_independently() -> None:
    # Hand-verified: a steady $40,000/min flow (delta=400, last=$1.00) for
    # 15 straight minutes sums to $560,000 (the first finalized minute is
    # always $0 — no delta exists until a second reading arrives — so it's
    # 1x$0 + 14x$40,000), crossing sustained_flow_min ($500,000) on the
    # 15th finalized minute. $40,000/min never crosses Unusual on its own
    # (average converges to $40,000 too, and 40,000 is not > 40,000*3).
    engine = WhaleAlertsEngine(InMemoryStorage())
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
    engine = WhaleAlertsEngine(InMemoryStorage())
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


# --- process_trade() / Lee-Ready — separate from the OptionChain-based
# process()/BVC tests above; see flow.py's _trade_states for why these two
# paths keep fully independent per-contract state.

TRADE_SYMBOL = "IWM"
TRADE_OCC_SYMBOL = "IWM260220C00185000"
TRADE_BASE_TIME = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
# A midpoint far below any premium/size combination used in these tests —
# every trade here classifies as a clean quote-rule BUY regardless of its
# own premium, so tests about bucketing/thresholds don't also have to
# reason about the buy/sell split (that's covered by the dedicated
# split-specific tests below, and by tests/test_lee_ready.py for the pure
# function itself).
BUY_LEANING_QUOTE = LatestQuote(bid=Decimal("0.01"), ask=Decimal("0.02"), as_of=TRADE_BASE_TIME)


def _trade(period: int, premium: str, size: int = 1) -> FlowEvent:
    return FlowEvent(
        symbol=TRADE_SYMBOL,
        occ_symbol=TRADE_OCC_SYMBOL,
        as_of=TRADE_BASE_TIME + timedelta(minutes=period),
        event_type=FlowEventType.UNUSUAL,
        premium=Decimal(premium),
        size=size,
        aggressor_side=Side.UNKNOWN,
    )


def test_process_trade_emits_unusual_with_a_full_buy_classification() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage())

    for period in range(6):
        assert engine.process_trade(_trade(period, "100"), BUY_LEANING_QUOTE) == ()

    # Lands in period 6's still-open bucket.
    engine.process_trade(_trade(6, "45000"), BUY_LEANING_QUOTE)
    # Period 7's trade finalizes period 6's bucket — this is what gets
    # classified against the trailing 5-period average built above.
    alerts = engine.process_trade(_trade(7, "100"), BUY_LEANING_QUOTE)

    assert len(alerts) == 1
    assert alerts[0].alert_type is WhaleAlertType.UNUSUAL
    assert alerts[0].amount == Decimal("45000")
    # Quote rule: every trade here prices well above BUY_LEANING_QUOTE's
    # midpoint, so the whole bucket is classified as buyer-initiated.
    assert alerts[0].estimated_buy_volume == Decimal("45000")
    assert alerts[0].estimated_sell_volume == Decimal("0")


def test_process_trade_classifies_a_trade_below_midpoint_as_a_full_sell() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage())
    # Midpoint (1000.02) sits above every trade's own derived price used in
    # this test (premium / (size * 100), at most 450 for the $45,000
    # trade) — the mirror image of BUY_LEANING_QUOTE, which sits below all
    # of them.
    sell_leaning_quote = LatestQuote(
        bid=Decimal("1000.00"), ask=Decimal("1000.04"), as_of=TRADE_BASE_TIME
    )

    for period in range(6):
        assert engine.process_trade(_trade(period, "100"), sell_leaning_quote) == ()

    engine.process_trade(_trade(6, "45000"), sell_leaning_quote)
    alerts = engine.process_trade(_trade(7, "100"), sell_leaning_quote)

    assert len(alerts) == 1
    assert alerts[0].estimated_buy_volume == Decimal("0")
    assert alerts[0].estimated_sell_volume == Decimal("45000")


def test_process_trade_neutral_split_when_no_quote_known_yet() -> None:
    # Documented edge case: no LatestQuote yet for this contract (e.g.
    # right at startup, before the Quote Stream's first message) — a
    # neutral 50/50 split, same convention as BVC's sigma == 0 fallback.
    engine = WhaleAlertsEngine(InMemoryStorage())

    for period in range(6):
        assert engine.process_trade(_trade(period, "100"), None) == ()

    engine.process_trade(_trade(6, "45000"), None)
    alerts = engine.process_trade(_trade(7, "100"), None)

    assert len(alerts) == 1
    assert alerts[0].estimated_buy_volume == Decimal("22500")
    assert alerts[0].estimated_sell_volume == Decimal("22500")
    assert alerts[0].estimated_buy_volume + alerts[0].estimated_sell_volume == Decimal("45000")


def test_process_trade_sustained_flow_fires_once() -> None:
    # Unlike process()/BVC, process_trade() has no "first call only
    # establishes a baseline, contributes $0" step — every trade
    # contributes its own premium to its own bucket immediately. A steady
    # $40,000/min flow across 16 straight minutes therefore finalizes 15
    # buckets of $40,000 each (periods 0-14, finalized as periods 1-15
    # arrive) for a total of $600,000, crossing sustained_flow_min
    # ($500,000) the moment the 15th bucket closes.
    engine = WhaleAlertsEngine(InMemoryStorage())

    for period in range(15):
        assert engine.process_trade(_trade(period, "40000"), BUY_LEANING_QUOTE) == ()

    # Finalizes period 14's bucket — the 15th push into the 15-slot
    # Sustained Flow window — so the alert fires on this same call.
    sustained_alerts = engine.process_trade(_trade(15, "40000"), BUY_LEANING_QUOTE)

    assert len(sustained_alerts) == 1
    assert sustained_alerts[0].alert_type is WhaleAlertType.SUSTAINED_FLOW
    assert sustained_alerts[0].amount == Decimal("600000")
    assert sustained_alerts[0].estimated_buy_volume == Decimal("600000")


def test_process_and_process_trade_never_share_state_for_the_same_contract() -> None:
    # Confirms the design decision behind _trade_states being a separate
    # dict from _states: under ThetaDataProvider, process() (still
    # polling for greeks/IV/OI) and process_trade() (the new streaming
    # path) can run concurrently for the very same occ_symbol without
    # either one's volume leaking into the other's bucket.
    engine = WhaleAlertsEngine(InMemoryStorage())
    base = MockDataProvider().get_option_chain("IWM")
    shared_occ_symbol = base.contracts[0].occ_symbol

    # Warm up process()'s own 5-period window exactly like the existing
    # OptionChain-based tests, interleaved period-by-period with an
    # unrelated, much larger process_trade() flow on the same occ_symbol.
    cumulative = 100
    engine.process(_chain(base, cumulative, 0))
    engine.process_trade(
        FlowEvent(
            symbol="IWM",
            occ_symbol=shared_occ_symbol,
            as_of=TRADE_BASE_TIME,
            event_type=FlowEventType.UNUSUAL,
            premium=Decimal("999999"),
            size=1,
            aggressor_side=Side.UNKNOWN,
        ),
        BUY_LEANING_QUOTE,
    )
    for period in range(1, 6):
        cumulative += 100
        assert engine.process(_chain(base, cumulative, period)) == ()

    cumulative += 450
    assert engine.process(_chain(base, cumulative, 6)) == ()
    alerts = engine.process(_chain(base, cumulative, 7))

    # Identical result to test_engine_emits_unusual_after_five_previous_periods
    # (which has no process_trade() calls at all) — proof the interleaved
    # $999,999 trade-stream premium never touched process()'s own bucket.
    assert len(alerts) == 1
    assert alerts[0].alert_type is WhaleAlertType.UNUSUAL
    assert alerts[0].amount == Decimal("45000.00")
