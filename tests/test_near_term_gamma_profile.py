from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.thetadata.greeks import PassthroughGreeksCalculator
from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases import (
    CalculateGammaAggregateUseCase,
    CalculateGreeksUseCase,
    CalculateNearTermGammaProfileUseCase,
)
from backend.domain.use_cases.errors import NotFoundError

AS_OF = datetime(2026, 9, 21, 14, 30, tzinfo=UTC)
NEAREST_EXPIRATION = date(2026, 9, 21)


def _contract(strike: Decimal, expiration: date, open_interest: int) -> OptionContract:
    return OptionContract(
        underlying="SPX",
        strike=strike,
        expiration=expiration,
        contract_type=ContractType.CALL,
        occ_symbol=f"SPX{expiration.isoformat()}C{strike}",
        bid=Decimal(1),
        ask=Decimal("1.10"),
        last=Decimal("1.05"),
        volume=0,
        open_interest=open_interest,
        iv=Decimal("0.20"),
        greeks=Greeks(
            delta=Decimal("0.50"),
            gamma=Decimal("0.01"),
            theta=Decimal("-0.10"),
            vega=Decimal("0.20"),
            charm=Decimal("0.01"),
            vanna=Decimal("0.02"),
        ),
    )


def _use_case(storage: InMemoryStorage, window_days: int = 30) -> CalculateNearTermGammaProfileUseCase:
    return CalculateNearTermGammaProfileUseCase(
        storage=storage,
        greeks=CalculateGreeksUseCase(PassthroughGreeksCalculator()),
        aggregate=CalculateGammaAggregateUseCase(
            FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()
        ),
        window_days=window_days,
    )


def test_excludes_a_far_dated_outlier_expiration_from_the_near_term_profile() -> None:
    # Mirrors the real SPX incident (2026-09-21): a book uniformly tight
    # near the money across every real near-term expiration, plus one
    # LEAPS listing (1,915 DTE here rounds to ~5 years, matching the real
    # one found live) contributing a real, non-zero-OI strike far away --
    # open_interest alone can't tell these apart (see
    # _filter_to_near_term_expirations' own module-level comment), only
    # expiration can.
    storage = InMemoryStorage()
    chain = OptionChain(
        symbol="SPX",
        as_of=AS_OF,
        spot_price=Decimal(7755),
        contracts=(
            _contract(Decimal(7700), NEAREST_EXPIRATION, open_interest=364720),
            _contract(Decimal(7750), date(2026, 9, 30), open_interest=126974),
            _contract(Decimal(7200), date(2031, 12, 19), open_interest=862),
        ),
    )
    storage.save_chain_snapshot(chain)

    result = _use_case(storage).execute("SPX")

    strikes = {item.strike for item in result.items}
    assert strikes == {Decimal(7700), Decimal(7750)}
    assert Decimal(7200) not in strikes


def test_keeps_every_contract_when_only_one_expiration_exists() -> None:
    # The common case (every equity/ETF today) -- must behave exactly like
    # the full-book orchestrator when there's nothing to filter out.
    storage = InMemoryStorage()
    chain = OptionChain(
        symbol="AAPL",
        as_of=AS_OF,
        spot_price=Decimal(338),
        contracts=(
            _contract(Decimal(335), NEAREST_EXPIRATION, open_interest=8000),
            _contract(Decimal(340), NEAREST_EXPIRATION, open_interest=6000),
        ),
    )
    storage.save_chain_snapshot(chain)

    result = _use_case(storage).execute("AAPL")

    strikes = {item.strike for item in result.items}
    assert strikes == {Decimal(335), Decimal(340)}


def test_keeps_a_contract_exactly_at_the_window_cutoff_and_drops_one_day_past_it() -> None:
    storage = InMemoryStorage()
    at_cutoff = NEAREST_EXPIRATION + timedelta(days=30)
    past_cutoff = NEAREST_EXPIRATION + timedelta(days=31)
    chain = OptionChain(
        symbol="SPX",
        as_of=AS_OF,
        spot_price=Decimal(7755),
        contracts=(
            _contract(Decimal(7700), NEAREST_EXPIRATION, open_interest=1000),
            _contract(Decimal(7710), at_cutoff, open_interest=1000),
            _contract(Decimal(7720), past_cutoff, open_interest=1000),
        ),
    )
    storage.save_chain_snapshot(chain)

    result = _use_case(storage, window_days=30).execute("SPX")

    strikes = {item.strike for item in result.items}
    assert strikes == {Decimal(7700), Decimal(7710)}
    assert Decimal(7720) not in strikes


def test_raises_not_found_when_no_chain_is_stored() -> None:
    storage = InMemoryStorage()

    with pytest.raises(NotFoundError, match="SPX"):
        _use_case(storage).execute("SPX")


def test_is_not_persisted() -> None:
    # Deliberately does not call storage.save_gamma_aggregate -- this is a
    # read-time-only view for the chart, must never overwrite the real,
    # full-book GammaAggregate that Gamma Flip/Walls/Max Pain depend on.
    # AAPL, not SPX -- InMemoryStorage.get_latest_chain_snapshot applies
    # the same "an index needs >1 distinct expiration" guard the real
    # PostgreSQLStorage does (see that method's own comment), which a
    # single-contract SPX fixture would legitimately fail.
    storage = InMemoryStorage()
    chain = OptionChain(
        symbol="AAPL",
        as_of=AS_OF,
        spot_price=Decimal(338),
        contracts=(_contract(Decimal(335), NEAREST_EXPIRATION, open_interest=1000),),
    )
    storage.save_chain_snapshot(chain)

    _use_case(storage).execute("AAPL")

    assert storage.get_latest_gamma_aggregate("AAPL") is None
