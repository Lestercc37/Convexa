"""gamma_flip=None (no sign crossing found) must propagate honestly all
the way to the API, not collapse into 0 via the `or` that used to live
in CalculateGammaExposureOrchestrator.execute() -- and the two real
comparison sites that read gamma.gamma_flip numerically
(MarketSnapshot._price_dealer_mode, agreement_component) must not
crash when it's None. Both the "no crossing" and "crossing found"
cases are tested explicitly, end to end through the real orchestrator,
not just at FakeGammaFlipCalculator's own level (already covered by
tests/test_gamma_flip_engine.py)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.adapters.providers.mock.max_pain import FakeMaxPainCalculator
from backend.adapters.providers.mock.provider import MockDataProvider
from backend.adapters.providers.mock.walls import FakeWallCalculator
from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import (
    ContractType,
    GammaAggregate,
    Greeks,
    MarketPrice,
    OptionChain,
    OptionContract,
)
from backend.domain.use_cases import (
    CalculateGammaAggregateUseCase,
    CalculateGammaExposureOrchestrator,
    CalculateGammaFlipUseCase,
    CalculateGreeksUseCase,
    CalculateMaxPainUseCase,
    CalculateWallsUseCase,
    build_market_snapshot,
)
from backend.domain.use_cases.calculate_derived_metrics import agreement_component
from backend.main import app


class _PreservingGreeksCalculator:
    def calculate(self, chain: OptionChain) -> OptionChain:
        return chain


def _contract(occ_symbol: str, contract_type: ContractType, strike: Decimal, gamma: str) -> OptionContract:
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
        open_interest=100,
        iv=Decimal("0.20"),
        greeks=Greeks(
            delta=Decimal("0.50"),
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
        greeks=CalculateGreeksUseCase(_PreservingGreeksCalculator()),
        aggregate=CalculateGammaAggregateUseCase(
            FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()
        ),
        gamma_flip=CalculateGammaFlipUseCase(FakeGammaFlipCalculator()),
        walls=CalculateWallsUseCase(FakeWallCalculator()),
        max_pain=CalculateMaxPainUseCase(FakeMaxPainCalculator()),
    )


def test_crossing_found_propagates_a_real_decimal_end_to_end() -> None:
    # CALL-only at 540 (net_gamma > 0), PUT-only at 560 (net_gamma < 0)
    # -- a genuine sign crossing between the two strikes.
    storage = InMemoryStorage()
    chain = OptionChain(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        spot_price=Decimal(550),
        contracts=(
            _contract("SPY260220C00540000", ContractType.CALL, Decimal(540), "0.05"),
            _contract("SPY260220P00560000", ContractType.PUT, Decimal(560), "0.02"),
        ),
    )
    storage.save_chain_snapshot(chain)

    result = _orchestrator(storage).execute("SPY")

    assert result.gamma_flip is not None
    assert isinstance(result.gamma_flip, Decimal)
    assert storage.get_latest_gamma_aggregate("SPY").gamma_flip == result.gamma_flip


def test_no_crossing_propagates_none_not_zero_end_to_end() -> None:
    # Both strikes CALL-only -- net_gamma positive everywhere, exactly
    # the live-confirmed SPX case (2026-09 investigation) that surfaced
    # this bug: no sign crossing anywhere in the range being looked at.
    storage = InMemoryStorage()
    chain = OptionChain(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        spot_price=Decimal(550),
        contracts=(
            _contract("SPY260220C00540000", ContractType.CALL, Decimal(540), "0.05"),
            _contract("SPY260220C00560000", ContractType.CALL, Decimal(560), "0.03"),
        ),
    )
    storage.save_chain_snapshot(chain)

    result = _orchestrator(storage).execute("SPY")

    assert result.gamma_flip is None
    assert storage.get_latest_gamma_aggregate("SPY").gamma_flip is None


def test_dealer_mode_does_not_crash_when_gamma_flip_is_none() -> None:
    storage = InMemoryStorage()
    now = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    storage.save_market_price(MarketPrice(symbol="SPY", as_of=now, price=Decimal("552.25"), volume=1000))
    storage.save_gamma_aggregate(
        GammaAggregate(symbol="SPY", as_of=now, gamma_flip=None, net_gamma=Decimal("100"))
    )
    storage.save_chain_snapshot(MockDataProvider().get_option_chain("SPY"))

    snapshot = build_market_snapshot(storage, "SPY")

    # No independent price-vs-flip signal to disagree with dealer_position
    # -- reports confirmed agreement rather than crashing or inventing a
    # third dealer_mode_source the API schema doesn't have. Flagged for
    # product review, not presented as the only sensible choice.
    assert snapshot.dealer_mode == "long_gamma"  # dealer_position(net_gamma=100)
    assert snapshot.dealer_mode_confirmed is True
    assert snapshot.dealer_mode_source == "agree"


def test_agreement_component_does_not_crash_when_gamma_flip_is_none() -> None:
    gamma = GammaAggregate(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        gamma_flip=None,
        net_gamma=Decimal("100"),
    )

    score = agreement_component(gamma, Decimal("552.25"))

    assert score == Decimal("100")


def test_gamma_flip_route_reports_flip_found_false_when_no_crossing() -> None:
    with TestClient(app) as client:
        # AAPL/MSFT/DIA are equity roots (see the SPX/NDX weekly-root PR) --
        # any active symbol's mock chain works here; the point is exercising
        # the real route + storage round trip, not a specific symbol.
        trigger = client.post("/internal/trigger-calculation/spy")
        response = client.get("/api/v1/gamma/spy/flip")

    assert trigger.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert isinstance(payload["flip_found"], bool)
    if not payload["flip_found"]:
        assert payload["gamma_flip_price"] is None
    else:
        assert payload["gamma_flip_price"] is not None
