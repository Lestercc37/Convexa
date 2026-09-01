from decimal import Decimal

from backend.domain.entities import Side
from backend.domain.use_cases.calculate_lee_ready import classify_trade_side


def test_trade_clearly_above_midpoint_is_buyer_initiated() -> None:
    # Midpoint of (10.00, 10.20) is 10.10 — 10.15 is clearly above it.
    side = classify_trade_side(
        price=Decimal("10.15"),
        bid=Decimal("10.00"),
        ask=Decimal("10.20"),
        previous_price=Decimal("10.05"),
        previous_side=Side.SELL,
    )

    assert side is Side.BUY


def test_trade_clearly_below_midpoint_is_seller_initiated() -> None:
    side = classify_trade_side(
        price=Decimal("10.05"),
        bid=Decimal("10.00"),
        ask=Decimal("10.20"),
        previous_price=Decimal("10.15"),
        previous_side=Side.BUY,
    )

    assert side is Side.SELL


def test_trade_at_midpoint_falls_back_to_tick_rule_higher_price_is_buyer() -> None:
    side = classify_trade_side(
        price=Decimal("10.10"),
        bid=Decimal("10.00"),
        ask=Decimal("10.20"),
        previous_price=Decimal("10.05"),
        previous_side=Side.SELL,
    )

    assert side is Side.BUY


def test_trade_at_midpoint_falls_back_to_tick_rule_lower_price_is_seller() -> None:
    side = classify_trade_side(
        price=Decimal("10.10"),
        bid=Decimal("10.00"),
        ask=Decimal("10.20"),
        previous_price=Decimal("10.15"),
        previous_side=Side.BUY,
    )

    assert side is Side.SELL


def test_trade_at_midpoint_with_a_zero_tick_carries_forward_previous_side() -> None:
    # Exactly at the midpoint AND exactly the same price as the previous
    # trade — the tick rule has no direction to compare, so it carries
    # forward whatever the previous trade was classified as.
    side = classify_trade_side(
        price=Decimal("10.10"),
        bid=Decimal("10.00"),
        ask=Decimal("10.20"),
        previous_price=Decimal("10.10"),
        previous_side=Side.SELL,
    )

    assert side is Side.SELL


def test_trade_at_midpoint_with_no_previous_price_is_neutral() -> None:
    # First trade ever seen for this contract, printing exactly at the
    # midpoint — no tick-rule direction available at all.
    side = classify_trade_side(
        price=Decimal("10.10"),
        bid=Decimal("10.00"),
        ask=Decimal("10.20"),
        previous_price=None,
        previous_side=Side.UNKNOWN,
    )

    assert side is Side.UNKNOWN


def test_no_prevailing_quote_yet_is_neutral_regardless_of_tick_rule() -> None:
    # Documented edge case: no quote known yet for this contract (e.g.
    # right at startup, before the first Quote Stream message) — same
    # "no informative signal yet" convention as BVC's sigma == 0 and
    # calculate_iv_rank's flat-window fallback. Takes priority even when
    # a tick-rule direction would otherwise be available.
    side = classify_trade_side(
        price=Decimal("10.15"),
        bid=None,
        ask=None,
        previous_price=Decimal("10.05"),
        previous_side=Side.BUY,
    )

    assert side is Side.UNKNOWN


def test_missing_only_one_side_of_the_quote_is_still_neutral() -> None:
    side_missing_bid = classify_trade_side(
        price=Decimal("10.15"),
        bid=None,
        ask=Decimal("10.20"),
        previous_price=Decimal("10.05"),
        previous_side=Side.BUY,
    )
    side_missing_ask = classify_trade_side(
        price=Decimal("10.15"),
        bid=Decimal("10.00"),
        ask=None,
        previous_price=Decimal("10.05"),
        previous_side=Side.BUY,
    )

    assert side_missing_bid is Side.UNKNOWN
    assert side_missing_ask is Side.UNKNOWN
