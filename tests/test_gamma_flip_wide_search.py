from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.adapters.providers.mock.max_pain import FakeMaxPainCalculator
from backend.adapters.providers.mock.walls import FakeWallCalculator
from backend.adapters.providers.thetadata.greeks import PassthroughGreeksCalculator
from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases import (
    CalculateGammaAggregateUseCase,
    CalculateGammaExposureOrchestrator,
    CalculateGammaFlipUseCase,
    CalculateGreeksUseCase,
    CalculateMaxPainUseCase,
    CalculateWallsUseCase,
)

SPOT = Decimal(550)
AS_OF = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
EXPIRATION = date(2026, 1, 16)

# No daily bars saved in InMemoryStorage below, so both the narrow and
# wide near-the-money widths fall back to
# INSUFFICIENT_DATA_WIDTH_FRACTION (0.02) x spot, scaled by each
# multiplier's own ratio -- narrow (1.5x): 550*0.02 = 11 (539-561, keeps
# only the two strikes 5 away from spot); wide (4x): 550*0.02*(4/1.5) =
# 29.33 (520.67-579.33, wide enough to also reach the strike 25 away).


def _contract(occ_symbol: str, contract_type: ContractType, strike: Decimal, open_interest: int, gamma: str) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        strike=strike,
        expiration=EXPIRATION,
        contract_type=contract_type,
        occ_symbol=occ_symbol,
        bid=Decimal(1),
        ask=Decimal("1.10"),
        last=Decimal("1.05"),
        volume=0,
        open_interest=open_interest,
        iv=Decimal("0.20"),
        greeks=Greeks(
            delta=Decimal("0.50") if contract_type == ContractType.CALL else Decimal("-0.50"),
            gamma=Decimal(gamma),
            theta=Decimal("-0.10"),
            vega=Decimal("0.20"),
            charm=Decimal("0.01"),
            vanna=Decimal("0.02"),
        ),
    )


def _orchestrator(storage: InMemoryStorage) -> CalculateGammaExposureOrchestrator:
    return CalculateGammaExposureOrchestrator(
        storage=storage,
        greeks=CalculateGreeksUseCase(PassthroughGreeksCalculator()),
        aggregate=CalculateGammaAggregateUseCase(
            FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()
        ),
        gamma_flip=CalculateGammaFlipUseCase(FakeGammaFlipCalculator()),
        walls=CalculateWallsUseCase(FakeWallCalculator()),
        max_pain=CalculateMaxPainUseCase(FakeMaxPainCalculator()),
    )


def test_gamma_flip_finds_a_crossing_outside_the_narrow_walls_width() -> None:
    # Strike 525 (25 away, outside the ~11-wide narrow width but inside
    # the ~29-wide Gamma Flip search) is put-dominant (net gamma
    # negative); both narrow strikes (545, 555, each 5 away) are
    # call-dominant (net gamma positive) -- no crossing exists within
    # the narrow width alone, only once the wider search reaches 525.
    contracts = (
        _contract("SPY260116C00525000", ContractType.CALL, Decimal(525), 5, "0.05"),
        _contract("SPY260116P00525000", ContractType.PUT, Decimal(525), 50, "0.05"),
        _contract("SPY260116C00545000", ContractType.CALL, Decimal(545), 50, "0.05"),
        _contract("SPY260116P00545000", ContractType.PUT, Decimal(545), 5, "0.05"),
        _contract("SPY260116C00555000", ContractType.CALL, Decimal(555), 50, "0.05"),
        _contract("SPY260116P00555000", ContractType.PUT, Decimal(555), 5, "0.05"),
    )
    chain = OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT, contracts=contracts)
    storage = InMemoryStorage()
    storage.save_chain_snapshot(chain)

    result = _orchestrator(storage).execute("SPY")

    assert result.gamma_flip is not None
    # The crossing sits between the negative 525 and the positive 545 --
    # confirms it was found using the wide search, not fabricated.
    assert Decimal(525) < result.gamma_flip < Decimal(545)
    # Call Wall/Put Wall/the persisted per-strike items stay scoped to
    # the narrow width -- the far (525) strike must not leak into them,
    # or this would silently reintroduce the same "distant strike
    # distorts near-term levels" bug the near-term expiration filter
    # was built to fix, just via distance instead of expiration date.
    assert {item.strike for item in result.items} == {Decimal(545), Decimal(555)}


def test_regime_reflects_the_wide_book_not_just_the_narrow_walls_window() -> None:
    # Confirmed with the user, 2026-09-24, from a real trading scenario:
    # price crossing a LOCAL zero-crossing near a support level doesn't
    # mean the regime that actually governs dealer hedging flow has
    # changed, if the broader book is still dominated by the opposite
    # sign -- a regime reading tied to the ATR-narrow walls window alone
    # would have been actively misleading there, not just cosmetically
    # inconsistent with the Gamma Flip line.
    #
    # Strike 525 (outside the ~11-wide narrow width, inside the ~29-wide
    # Gamma Flip search) is heavily put-dominant (5 call OI vs 500 put
    # OI) -- a large negative net_gamma sitting just outside the narrow
    # radius. Both narrow strikes (545, 555) are call-dominant
    # (positive). Under the old narrow-only regime calculation, only
    # 545/555 would be summed -- both positive, "long_gamma". The real,
    # complete book is net short.
    contracts = (
        _contract("SPY260116C00525000", ContractType.CALL, Decimal(525), 5, "0.05"),
        _contract("SPY260116P00525000", ContractType.PUT, Decimal(525), 500, "0.05"),
        _contract("SPY260116C00545000", ContractType.CALL, Decimal(545), 50, "0.05"),
        _contract("SPY260116P00545000", ContractType.PUT, Decimal(545), 5, "0.05"),
        _contract("SPY260116C00555000", ContractType.CALL, Decimal(555), 50, "0.05"),
        _contract("SPY260116P00555000", ContractType.PUT, Decimal(555), 5, "0.05"),
    )
    chain = OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT, contracts=contracts)
    storage = InMemoryStorage()
    storage.save_chain_snapshot(chain)

    result = _orchestrator(storage).execute("SPY")

    # The narrow window (still used for the GEX-by-strike histogram/
    # items, Call Wall/Put Wall/Max Pain -- unchanged) never sees 525.
    assert {item.strike for item in result.items} == {Decimal(545), Decimal(555)}
    # But the regime/Net GEX totals now correctly reflect it: net short,
    # not the "long_gamma" the narrow window alone would have implied.
    assert result.net_gamma == Decimal("-6125625.0000")
    assert result.positive_gamma == Decimal("1361250.0000")
    assert result.negative_gamma == Decimal("-7486875.0000")
    assert result.dealer_position == "short_gamma"


def test_gamma_flip_is_still_none_when_no_crossing_exists_even_in_the_wide_search() -> None:
    # Every strike (near and far) is call-dominant -- no crossing
    # anywhere, wide search included. Must not fabricate one.
    contracts = (
        _contract("SPY260116C00525000", ContractType.CALL, Decimal(525), 50, "0.05"),
        _contract("SPY260116P00525000", ContractType.PUT, Decimal(525), 5, "0.05"),
        _contract("SPY260116C00545000", ContractType.CALL, Decimal(545), 50, "0.05"),
        _contract("SPY260116P00545000", ContractType.PUT, Decimal(545), 5, "0.05"),
    )
    chain = OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT, contracts=contracts)
    storage = InMemoryStorage()
    storage.save_chain_snapshot(chain)

    result = _orchestrator(storage).execute("SPY")

    assert result.gamma_flip is None
