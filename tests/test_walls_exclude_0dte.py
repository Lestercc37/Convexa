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


def test_call_wall_and_put_wall_ignore_a_dominant_0dte_strike() -> None:
    """BSM gamma diverges as an ATM contract's time-to-expiry approaches
    zero (confirmed live, 2026-09-23, SPX: a 0DTE strike with thin open
    interest hijacked both walls away from GEXBot's own stable reference
    levels). Call Wall/Put Wall must select from the non-0DTE strikes even
    when the 0DTE strike's own dealer gamma exposure is by far the
    largest in the chain -- that concentration isn't a real multi-day
    dealer inventory wall, it's an artifact of the option's own imminent
    expiry."""
    storage = InMemoryStorage()
    # 2026-01-15 14:30 UTC is 09:30 ET (EST, UTC-5) -- expiration=date(2026,1,15)
    # is genuinely "today" in the market's own timezone, not just in UTC.
    as_of = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    spot_price = Decimal(550)
    contracts = (
        # 0DTE, dominant gamma exposure, modest open interest -- would win
        # both walls outright if included.
        _contract(
            "SPY260115C00550000", ContractType.CALL, Decimal(550), date(2026, 1, 15),
            open_interest=100, gamma="1.0",
        ),
        _contract(
            "SPY260115P00550000", ContractType.PUT, Decimal(550), date(2026, 1, 15),
            open_interest=100, gamma="1.0",
        ),
        # Multi-day, far smaller per-contract gamma, same open interest --
        # the only real candidates once 0DTE is excluded.
        _contract(
            "SPY260120C00551000", ContractType.CALL, Decimal(551), date(2026, 1, 20),
            open_interest=100, gamma="0.01",
        ),
        _contract(
            "SPY260120P00549000", ContractType.PUT, Decimal(549), date(2026, 1, 20),
            open_interest=100, gamma="0.01",
        ),
    )
    chain = OptionChain(symbol="SPY", as_of=as_of, spot_price=spot_price, contracts=contracts)
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

    assert result.call_wall == Decimal(551)
    assert result.put_wall == Decimal(549)
    # Net GEX/the GEX-by-strike items still reflect the 0DTE strike's real
    # dealer gamma -- only wall *selection* excludes it.
    assert any(item.strike == Decimal(550) for item in result.items)


def _contract(
    occ_symbol: str,
    contract_type: ContractType,
    strike: Decimal,
    expiration: date,
    open_interest: int,
    gamma: str,
) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        strike=strike,
        expiration=expiration,
        contract_type=contract_type,
        occ_symbol=occ_symbol,
        bid=Decimal(1),
        ask=Decimal("1.10"),
        last=Decimal("1.05"),
        volume=100,
        open_interest=open_interest,
        iv=Decimal("0.20"),
        greeks=Greeks(
            delta=Decimal("0.50") if contract_type == ContractType.CALL else Decimal("-0.50"),
            gamma=Decimal(gamma),
            theta=Decimal("-0.10"),
            vega=Decimal("0.20"),
            charm=Decimal("0.01"),
            vanna=Decimal("0.01"),
        ),
    )
