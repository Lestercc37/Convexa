from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases.gamma import (
    STRUCTURAL_WINDOW_DAYS_BY_SYMBOL,
    TACTICAL_WINDOW_DAYS,
    _filter_to_near_term_expirations,
    _structural_window_days,
)

AS_OF = datetime(2026, 9, 24, 14, 30, tzinfo=UTC)
NEAREST_EXPIRATION = date(2026, 9, 24)


# Replaced the flat 90-day window with a per-symbol tiered one, 2026-09-25
# (methodology document shared/reviewed with the user the same day): 30
# days for liquid indices/ETFs, 45 for individual stocks. See
# STRUCTURAL_WINDOW_DAYS_BY_SYMBOL's own comment in gamma.py for the full
# reasoning, including the /market/SPX slowness this connects to.
def test_liquid_indices_and_etfs_get_the_30_day_structural_window() -> None:
    for symbol in ("SPX", "NDX", "VIX", "SPY", "QQQ", "IWM", "DIA", "ES"):
        assert _structural_window_days(symbol) == 30, symbol


def test_individual_stocks_get_the_45_day_structural_window() -> None:
    for symbol in ("AAPL", "MSFT", "NVDA", "TSLA", "META", "AMZN", "GOOGL"):
        assert _structural_window_days(symbol) == 45, symbol


def test_every_active_underlying_is_explicitly_classified() -> None:
    # Guards against a future edit silently adding a symbol without
    # classifying it -- an unclassified symbol still works (falls back to
    # the wider 45-day tier), but should never happen by omission.
    from backend.domain.underlyings import ACTIVE_UNDERLYINGS

    for underlying in ACTIVE_UNDERLYINGS:
        assert underlying.symbol in STRUCTURAL_WINDOW_DAYS_BY_SYMBOL, underlying.symbol


def test_an_unclassified_symbol_falls_back_to_the_wider_conservative_tier() -> None:
    assert _structural_window_days("ZZZZ") == 45


def test_symbol_classification_is_case_insensitive() -> None:
    assert _structural_window_days("spy") == 30
    assert _structural_window_days("aapl") == 45


def test_tactical_window_is_fixed_at_2_days_regardless_of_symbol() -> None:
    assert TACTICAL_WINDOW_DAYS == 2


def test_a_contract_20_days_out_is_included_in_the_30_day_structural_window() -> None:
    chain = _chain_with_expirations(NEAREST_EXPIRATION, NEAREST_EXPIRATION + timedelta(days=20))

    filtered = _filter_to_near_term_expirations(chain, _structural_window_days("SPY"))

    assert {c.expiration for c in filtered.contracts} == {
        NEAREST_EXPIRATION,
        NEAREST_EXPIRATION + timedelta(days=20),
    }


def test_a_contract_40_days_out_is_excluded_from_the_30_day_structural_window() -> None:
    chain = _chain_with_expirations(NEAREST_EXPIRATION, NEAREST_EXPIRATION + timedelta(days=40))

    filtered = _filter_to_near_term_expirations(chain, _structural_window_days("SPY"))

    assert {c.expiration for c in filtered.contracts} == {NEAREST_EXPIRATION}


def test_a_contract_40_days_out_is_included_in_the_45_day_structural_window() -> None:
    chain = _chain_with_expirations(NEAREST_EXPIRATION, NEAREST_EXPIRATION + timedelta(days=40))

    filtered = _filter_to_near_term_expirations(chain, _structural_window_days("AAPL"))

    assert {c.expiration for c in filtered.contracts} == {
        NEAREST_EXPIRATION,
        NEAREST_EXPIRATION + timedelta(days=40),
    }


def test_a_leaps_contract_well_past_a_year_out_is_still_excluded() -> None:
    # The original reason this filter exists at all (a single far-dated
    # LEAPS listing distorting near-term magnitudes/chart range) --
    # confirms the tiered windows didn't quietly remove that guard, using
    # the widest (45-day) tier since that's the most permissive case.
    chain = _chain_with_expirations(
        NEAREST_EXPIRATION, NEAREST_EXPIRATION + timedelta(days=365 * 2)
    )

    filtered = _filter_to_near_term_expirations(chain, _structural_window_days("AAPL"))

    assert {c.expiration for c in filtered.contracts} == {NEAREST_EXPIRATION}


def test_structural_anchor_defaults_to_the_nearest_listed_expiration() -> None:
    # anchor=None (every structural caller) -- unchanged from before this
    # function gained an explicit anchor parameter: the window's own
    # lower bound is implicitly satisfied by the nearest LISTED
    # expiration, regardless of real "today".
    later_nearest = NEAREST_EXPIRATION + timedelta(days=5)
    chain = _chain_with_expirations(later_nearest, later_nearest + timedelta(days=10))

    filtered = _filter_to_near_term_expirations(chain, 30)

    assert {c.expiration for c in filtered.contracts} == {
        later_nearest,
        later_nearest + timedelta(days=10),
    }


def test_tactical_anchor_is_explicit_and_excludes_contracts_before_it() -> None:
    # anchor=<a real date> (tactical only) -- a contract listed BEFORE
    # the anchor must be excluded, unlike the structural (anchor=None)
    # case where the anchor IS the earliest listed contract by
    # construction. Proves the new lower-bound check does real work here.
    anchor = NEAREST_EXPIRATION + timedelta(days=3)
    chain = _chain_with_expirations(
        NEAREST_EXPIRATION,  # before the anchor -- must be excluded
        anchor,
        anchor + timedelta(days=2),
    )

    filtered = _filter_to_near_term_expirations(chain, 2, anchor=anchor)

    assert {c.expiration for c in filtered.contracts} == {anchor, anchor + timedelta(days=2)}


def test_tactical_anchor_with_nothing_listed_in_range_yields_an_empty_chain() -> None:
    # The honest-empty-result case (e.g. an individual stock with only
    # Friday weeklies, on a day that isn't within 2 real days of one) --
    # _filter_to_near_term_expirations itself just returns an empty
    # chain; CalculateGammaExposureOrchestrator._build_view is what turns
    # that into an honest empty GammaAggregate rather than falling back
    # to the full chain (covered in test_gamma_aggregate_engine.py).
    anchor = NEAREST_EXPIRATION
    chain = _chain_with_expirations(NEAREST_EXPIRATION + timedelta(days=10))

    filtered = _filter_to_near_term_expirations(chain, 2, anchor=anchor)

    assert filtered.contracts == ()


def _chain_with_expirations(*expirations: date) -> OptionChain:
    contracts = tuple(
        OptionContract(
            underlying="SPY",
            strike=Decimal(550),
            expiration=expiration,
            contract_type=ContractType.CALL,
            occ_symbol=f"SPY{expiration:%y%m%d}C00550000",
            bid=Decimal(1),
            ask=Decimal("1.10"),
            last=Decimal("1.05"),
            volume=100,
            open_interest=100,
            iv=Decimal("0.20"),
            greeks=Greeks(
                delta=Decimal("0.50"),
                gamma=Decimal("0.03"),
                theta=Decimal("-0.10"),
                vega=Decimal("0.20"),
                charm=Decimal("0.01"),
                vanna=Decimal("0.02"),
            ),
        )
        for expiration in expirations
    )
    return OptionChain(symbol="SPY", as_of=AS_OF, spot_price=Decimal(550), contracts=contracts)
