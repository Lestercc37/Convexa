from decimal import Decimal

from backend.domain.use_cases.calculate_bvc import (
    calculate_bvc_split,
    calculate_price_volatility,
    standard_normal_cdf,
)


def test_standard_normal_cdf_matches_known_table_values() -> None:
    # Well-known standard normal CDF table values.
    assert standard_normal_cdf(Decimal(0)) == Decimal("0.5")
    assert round(standard_normal_cdf(Decimal(1)), 4) == Decimal("0.8413")
    assert round(standard_normal_cdf(Decimal(-1)), 4) == Decimal("0.1587")
    assert round(standard_normal_cdf(Decimal("1.96")), 4) == Decimal("0.9750")


def test_price_volatility_is_population_stdev_of_the_window() -> None:
    # Hand-verified: deltas [1, -1, 1, -1] have mean 0, population
    # variance ((1)^2+(1)^2+(1)^2+(1)^2)/4 = 1, so stdev = 1 exactly.
    assert calculate_price_volatility(
        [Decimal("1"), Decimal("-1"), Decimal("1"), Decimal("-1")]
    ) == Decimal("1")


def test_price_volatility_of_empty_or_single_point_window_is_zero() -> None:
    assert calculate_price_volatility([]) == Decimal(0)
    assert calculate_price_volatility([Decimal("5")]) == Decimal(0)


def test_bvc_split_at_zero_z_score_is_an_even_split() -> None:
    # price_delta=0 -> Z=0 -> Phi(0)=0.5 exactly, regardless of sigma.
    buy, sell = calculate_bvc_split(Decimal(0), Decimal("2"), Decimal("1000"))

    assert buy == Decimal("500.0")
    assert sell == Decimal("500.0")
    assert buy + sell == Decimal("1000.0")


def test_bvc_split_hand_verified_against_known_z_and_cdf() -> None:
    # price_delta=2, sigma=2 -> Z=1.0 -> Phi(1.0)~=0.8413447460685429
    # (matches the standard normal table value verified above), applied
    # to a volume of 1000.
    buy, sell = calculate_bvc_split(Decimal("2"), Decimal("2"), Decimal("1000"))

    assert round(buy, 4) == Decimal("841.3447")
    assert round(sell, 4) == Decimal("158.6553")
    assert round(buy + sell, 4) == Decimal("1000.0000")


def test_bvc_split_negative_price_delta_skews_toward_selling() -> None:
    # price_delta=-2, sigma=2 -> Z=-1.0 -> Phi(-1.0)~=0.1586553, the
    # mirror image of the positive case above.
    buy, sell = calculate_bvc_split(Decimal("-2"), Decimal("2"), Decimal("1000"))

    assert round(buy, 4) == Decimal("158.6553")
    assert round(sell, 4) == Decimal("841.3447")


def test_bvc_split_falls_back_to_neutral_split_when_sigma_is_zero() -> None:
    # Documented edge case: zero (or not-yet-warmed-up) volatility falls
    # back to a neutral 50/50 split — same convention calculate_iv_rank
    # already uses for a flat/degenerate window — regardless of how large
    # price_delta itself is.
    buy, sell = calculate_bvc_split(Decimal("500"), Decimal(0), Decimal("100"))

    assert buy == Decimal("50.0")
    assert sell == Decimal("50.0")
