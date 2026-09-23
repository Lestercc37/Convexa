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


def test_orchestrator_sums_vanna_exposure_from_hand_built_chain() -> None:
    storage = InMemoryStorage()
    chain = _known_chain()
    storage.save_chain_snapshot(chain)
    orchestrator = CalculateGammaExposureOrchestrator(
        storage=storage,
        greeks=CalculateGreeksUseCase(PassthroughGreeksCalculator()),
        aggregate=CalculateGammaAggregateUseCase(
            FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()
        ),
        gamma_flip=CalculateGammaFlipUseCase(FakeGammaFlipCalculator()),
        walls=CalculateWallsUseCase(FakeWallCalculator()),
        max_pain=CalculateMaxPainUseCase(FakeMaxPainCalculator()),
    )

    result = orchestrator.execute("SPY")

    # Vega/Theta/Charm/Delta all carry the same Sigma(greek x OI x 100 x
    # spot_price) pattern as Vanna -- spot_price=550 below.
    assert result.vega_exposure == Decimal(440000)
    assert result.theta_exposure == Decimal(-275000)
    assert result.charm_exposure == Decimal(5500)
    assert result.vanna_exposure == Decimal(27500)
    # Delta Exposure (DEX): same Sigma(greek x OI x 100 x spot_price)
    # pattern as the other four, no call/put sign flip applied -- delta's
    # own sign already does that job (0.50 call, -0.30 put below).
    # (0.50 * 10 * 100 * 550) + (-0.30 * 20 * 100 * 550) = 275000 - 330000 = -55000.
    assert result.delta_exposure == Decimal(-55000)
    assert storage.get_latest_gamma_aggregate("SPY") == result


def test_delta_exposure_keeps_the_call_and_put_signs_from_the_real_quote() -> None:
    """DEX must never apply an extra dealer-style +1 call/-1 put flip
    (that convention belongs only to dealer_gamma_exposure, a separate
    calculation) -- a call's positive delta and a put's negative delta
    must each contribute with their own real sign, unmodified."""
    storage = InMemoryStorage()
    as_of = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    contracts = (
        _contract(
            "SPY260220C00540000", ContractType.CALL, Decimal(540), 10,
            "0.20", "-0.10", "0.05", "0.01", delta="0.60",
        ),
        _contract(
            "SPY260220P00560000", ContractType.PUT, Decimal(560), 10,
            "0.30", "-0.20", "-0.02", "0.02", delta="-0.60",
        ),
    )
    chain = OptionChain(symbol="SPY", as_of=as_of, spot_price=Decimal(550), contracts=contracts)
    storage.save_chain_snapshot(chain)
    orchestrator = CalculateGammaExposureOrchestrator(
        storage=storage,
        greeks=CalculateGreeksUseCase(PassthroughGreeksCalculator()),
        aggregate=CalculateGammaAggregateUseCase(
            FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()
        ),
        gamma_flip=CalculateGammaFlipUseCase(FakeGammaFlipCalculator()),
        walls=CalculateWallsUseCase(FakeWallCalculator()),
        max_pain=CalculateMaxPainUseCase(FakeMaxPainCalculator()),
    )

    result = orchestrator.execute("SPY")

    # Same open interest (10) and same delta magnitude (0.60) on each
    # side, opposite real signs -- they must cancel exactly to zero, not
    # double up as +1200 (which an extra dealer-style flip on top of an
    # already-signed delta would produce).
    assert result.delta_exposure == Decimal(0)


def _known_chain() -> OptionChain:
    as_of = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    contracts = (
        _contract(
            "SPY260220C00540000",
            ContractType.CALL,
            Decimal(540),
            10,
            "0.20",
            "-0.10",
            "0.05",
            "0.01",
            delta="0.50",
        ),
        _contract(
            "SPY260220P00560000",
            ContractType.PUT,
            Decimal(560),
            20,
            "0.30",
            "-0.20",
            "-0.02",
            "0.02",
            delta="-0.30",
        ),
    )
    return OptionChain(
        symbol="SPY",
        as_of=as_of,
        spot_price=Decimal(550),
        contracts=contracts,
    )


def _contract(
    occ_symbol: str,
    contract_type: ContractType,
    strike: Decimal,
    open_interest: int,
    vega: str,
    theta: str,
    charm: str,
    vanna: str,
    delta: str = "0.50",
) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        strike=strike,
        expiration=date(2026, 2, 20),
        contract_type=contract_type,
        occ_symbol=occ_symbol,
        bid=Decimal(1),
        ask=Decimal("1.10"),
        last=Decimal("1.05"),
        volume=100,
        open_interest=open_interest,
        iv=Decimal("0.20"),
        greeks=Greeks(
            delta=Decimal(delta),
            gamma=Decimal("0.01"),
            theta=Decimal(theta),
            vega=Decimal(vega),
            charm=Decimal(charm),
            vanna=Decimal(vanna),
        ),
    )
