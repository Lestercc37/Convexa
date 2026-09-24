from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases.gamma import (
    NEAR_TERM_GAMMA_PROFILE_WINDOW_DAYS,
    _filter_to_near_term_expirations,
)

AS_OF = datetime(2026, 9, 24, 14, 30, tzinfo=UTC)
NEAREST_EXPIRATION = date(2026, 9, 24)


def test_window_is_90_days() -> None:
    # Widened 30 -> 90, 2026-09-24, per the user's own direction (the ~90
    # day + 0DTE convention referenced across GEX literature/reference
    # platforms) -- not a correction of the prior 30-day value, a
    # deliberate adjustment to observe against live data and iterate
    # from. Locks the value in so a future accidental revert is caught.
    assert NEAR_TERM_GAMMA_PROFILE_WINDOW_DAYS == 90


def test_a_contract_60_days_out_is_now_included() -> None:
    # Would have been excluded under the old 30-day window -- this is
    # the concrete, live-relevant behavior change the widening exists
    # to produce.
    chain = _chain_with_expirations(NEAREST_EXPIRATION, NEAREST_EXPIRATION + timedelta(days=60))

    filtered = _filter_to_near_term_expirations(chain, NEAR_TERM_GAMMA_PROFILE_WINDOW_DAYS)

    assert {c.expiration for c in filtered.contracts} == {
        NEAREST_EXPIRATION,
        NEAREST_EXPIRATION + timedelta(days=60),
    }


def test_a_leaps_contract_well_past_a_year_out_is_still_excluded() -> None:
    # The original reason this filter exists at all (a single far-dated
    # LEAPS listing distorting near-term magnitudes/chart range) --
    # confirms widening to 90 days didn't quietly remove that guard.
    chain = _chain_with_expirations(
        NEAREST_EXPIRATION, NEAREST_EXPIRATION + timedelta(days=365 * 2)
    )

    filtered = _filter_to_near_term_expirations(chain, NEAR_TERM_GAMMA_PROFILE_WINDOW_DAYS)

    assert {c.expiration for c in filtered.contracts} == {NEAREST_EXPIRATION}


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
