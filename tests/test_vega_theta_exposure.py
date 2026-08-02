from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.adapters.providers.mock.max_pain import FakeMaxPainCalculator
from backend.adapters.providers.mock.walls import FakeWallCalculator
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


class PreservingGreeksCalculator:
    """Keep the hand-built Greeks unchanged for the exposure test."""

    def calculate(self, chain: OptionChain) -> OptionChain:
        return chain


def test_orchestrator_sums_vega_and_theta_exposure_without_spot_squared() -> None:
    storage = InMemoryStorage()
    chain = _known_chain()
    storage.save_chain_snapshot(chain)
    orchestrator = CalculateGammaExposureOrchestrator(
        storage=storage,
        greeks=CalculateGreeksUseCase(PreservingGreeksCalculator()),
        aggregate=CalculateGammaAggregateUseCase(
            FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()
        ),
        gamma_flip=CalculateGammaFlipUseCase(FakeGammaFlipCalculator()),
        walls=CalculateWallsUseCase(FakeWallCalculator()),
        max_pain=CalculateMaxPainUseCase(FakeMaxPainCalculator()),
    )

    result = orchestrator.execute("SPY")

    assert result.vega_exposure == Decimal(800)
    assert result.theta_exposure == Decimal(-500)
    assert storage.get_latest_gamma_aggregate("SPY") == result


def _known_chain() -> OptionChain:
    as_of = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    contracts = (
        _contract("SPY260220C00540000", ContractType.CALL, Decimal(540), 10, "0.20", "-0.10"),
        _contract("SPY260220P00560000", ContractType.PUT, Decimal(560), 20, "0.30", "-0.20"),
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
            delta=Decimal("0.50"),
            gamma=Decimal("0.01"),
            theta=Decimal(theta),
            vega=Decimal(vega),
            charm=Decimal(0),
            vanna=Decimal(0),
        ),
    )
