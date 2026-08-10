from datetime import UTC, datetime
from decimal import Decimal

from backend.domain.entities import GammaAggregate, GammaAggregateItem
from backend.domain.use_cases import (
    CLOSING_WINDOW_THRESHOLD_PCT,
    calculate_charm_regime,
    calculate_closing_dynamics,
    calculate_pin_risk_score,
    calculate_vanna_interpretation,
)

# Hand-verified breakdown (dashboard-spec.md section 9 formula):
#   OI concentration (30%): top 3 of 4 strikes hold 4500/4700 OI (~95.7%),
#     saturates at 50% -> ratio 1.0 -> 30 points.
#   Proximity (25%): magnet strike (550, |net_gamma|=200, the largest) sits
#     exactly at the given price -> 0% distance, within the 0.5% saturation
#     -> ratio 1.0 -> 25 points.
#   Time (25%): time_to_close_pct=40 -> (100-40)/100=0.6 -> 15 points.
#   Gamma (20%): median |net_gamma| across the 4 strikes (50, 200, 30, 10)
#     is (30+50)/2=40; magnet's 200 against 10x that (400) -> 0.5 ratio
#     -> 10 points.
#   Total: 30 + 25 + 15 + 10 = 80.
_ITEMS = (
    GammaAggregateItem(
        strike=Decimal("545"),
        total_gamma_exposure=Decimal("0"),
        call_gamma_exposure=Decimal("0"),
        put_gamma_exposure=Decimal("0"),
        net_gamma=Decimal("50"),
        contract_count=1,
        open_interest=1000,
    ),
    GammaAggregateItem(
        strike=Decimal("550"),
        total_gamma_exposure=Decimal("0"),
        call_gamma_exposure=Decimal("0"),
        put_gamma_exposure=Decimal("0"),
        net_gamma=Decimal("200"),
        contract_count=1,
        open_interest=3000,
    ),
    GammaAggregateItem(
        strike=Decimal("555"),
        total_gamma_exposure=Decimal("0"),
        call_gamma_exposure=Decimal("0"),
        put_gamma_exposure=Decimal("0"),
        net_gamma=Decimal("-30"),
        contract_count=1,
        open_interest=500,
    ),
    GammaAggregateItem(
        strike=Decimal("560"),
        total_gamma_exposure=Decimal("0"),
        call_gamma_exposure=Decimal("0"),
        put_gamma_exposure=Decimal("0"),
        net_gamma=Decimal("10"),
        contract_count=1,
        open_interest=200,
    ),
)


def test_pin_risk_score_matches_hand_computed_weighted_breakdown() -> None:
    score, magnet_strike = calculate_pin_risk_score(
        _ITEMS, price=Decimal("550"), time_to_close_pct=Decimal("40")
    )

    assert magnet_strike == Decimal("550")
    assert score == Decimal("80")


def test_pin_risk_score_saturates_each_component_at_its_documented_threshold() -> None:
    # Deliberately different items from `_ITEMS`: here every component
    # individually reaches its saturation point (100% OI concentration
    # since there are only 3 strikes, price exactly on the magnet, at the
    # close, magnet gamma far past 10x the median) — the sum must land
    # exactly on 100, not overshoot it despite every ratio individually
    # exceeding 1.0 before capping.
    saturated_items = (
        GammaAggregateItem(
            strike=Decimal("545"),
            total_gamma_exposure=Decimal("0"),
            call_gamma_exposure=Decimal("0"),
            put_gamma_exposure=Decimal("0"),
            net_gamma=Decimal("10"),
            contract_count=1,
            open_interest=100,
        ),
        GammaAggregateItem(
            strike=Decimal("550"),
            total_gamma_exposure=Decimal("0"),
            call_gamma_exposure=Decimal("0"),
            put_gamma_exposure=Decimal("0"),
            net_gamma=Decimal("1000"),
            contract_count=1,
            open_interest=3000,
        ),
        GammaAggregateItem(
            strike=Decimal("555"),
            total_gamma_exposure=Decimal("0"),
            call_gamma_exposure=Decimal("0"),
            put_gamma_exposure=Decimal("0"),
            net_gamma=Decimal("10"),
            contract_count=1,
            open_interest=100,
        ),
    )

    score, magnet_strike = calculate_pin_risk_score(
        saturated_items, price=Decimal("550"), time_to_close_pct=Decimal("0")
    )

    assert magnet_strike == Decimal("550")
    assert score == Decimal("100")


def test_pin_risk_score_without_a_strike_breakdown_only_scores_time_remaining() -> None:
    # No `items` (e.g. a GammaAggregate persisted before the items
    # migration) — the OI/proximity/gamma components (75% of the weight)
    # can't be computed without a magnet strike, so only the time
    # component (25%) contributes.
    score, magnet_strike = calculate_pin_risk_score(
        (), price=Decimal("550"), time_to_close_pct=Decimal("0")
    )

    assert magnet_strike is None
    assert score == Decimal("25")


def test_charm_regime_follows_the_sign_of_charm_exposure() -> None:
    assert calculate_charm_regime(Decimal("1")) == "time_decay_dealers_buy"
    assert calculate_charm_regime(Decimal("-1")) == "time_decay_dealers_sell"
    assert calculate_charm_regime(Decimal("0")) is None


def test_vanna_interpretation_follows_the_sign_of_vanna_exposure() -> None:
    assert calculate_vanna_interpretation(Decimal("1")) == "iv_increase_dealers_buy"
    assert calculate_vanna_interpretation(Decimal("-1")) == "iv_increase_dealers_sell"
    assert calculate_vanna_interpretation(Decimal("0")) is None


def test_closing_dynamics_is_active_only_within_the_calibrated_closing_window() -> None:
    assert CLOSING_WINDOW_THRESHOLD_PCT == Decimal(15)

    gamma = GammaAggregate(
        symbol="SPY",
        as_of=datetime(2026, 8, 3, 14, 30, tzinfo=UTC),
        items=_ITEMS,
        charm_exposure=Decimal("5000"),
        vanna_exposure=Decimal("-2000"),
        max_pain=Decimal("548"),
    )

    far_from_close = calculate_closing_dynamics(
        gamma, price=Decimal("550"), time_to_close_pct=Decimal("15.01")
    )
    at_the_threshold = calculate_closing_dynamics(
        gamma, price=Decimal("550"), time_to_close_pct=Decimal("15")
    )

    assert far_from_close.active is False
    assert at_the_threshold.active is True
    # The values themselves always exist, regardless of `active` — same
    # "the data always exists" pattern as AtrRange/AnchoredVwap; only the
    # visual-prominence decision (left to the frontend) depends on it.
    assert far_from_close.pin_score > 0
    assert far_from_close.magnet_strike == Decimal("550")
    assert far_from_close.charm_regime == "time_decay_dealers_buy"
    assert far_from_close.vanna_interpretation == "iv_increase_dealers_sell"
    assert far_from_close.max_pain == Decimal("548")
