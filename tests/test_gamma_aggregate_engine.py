from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.domain.use_cases import CalculateGammaAggregateUseCase
from backend.domain.entities import (
    ContractType,
    GammaAggregate,
    GammaAggregateItem,
    Greeks,
    OptionChain,
    OptionContract,
)
from backend.domain.ports import IGammaAggregateCalculator


def test_fake_gamma_aggregate_calculator_groups_gamma_exposure_by_strike() -> None:
    chain = _chain()
    exposures = FakeGammaExposureCalculator().calculate(chain)

    aggregate = FakeGammaAggregateCalculator().calculate(exposures, chain.symbol, chain.as_of)

    assert aggregate == GammaAggregate(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        items=(
            GammaAggregateItem(
                strike=Decimal("540"),
                total_gamma_exposure=Decimal("117975000.000"),
                call_gamma_exposure=Decimal("72600000.000"),
                put_gamma_exposure=Decimal("-45375000.000"),
                net_gamma=Decimal("27225000.000"),
                contract_count=2,
                absolute_gamma=Decimal("27225000.000"),
            ),
            GammaAggregateItem(
                strike=Decimal("545"),
                total_gamma_exposure=Decimal("63525000.000"),
                call_gamma_exposure=Decimal("60500000.000"),
                put_gamma_exposure=Decimal("-3025000.000"),
                net_gamma=Decimal("57475000.000"),
                contract_count=2,
                absolute_gamma=Decimal("57475000.000"),
            ),
        ),
        total_market_gamma=Decimal("84700000.000"),
        positive_gamma=Decimal("84700000.000"),
        negative_gamma=Decimal("0"),
        total_gamma=Decimal("84700000.000"),
        net_gamma=Decimal("84700000.000"),
        dealer_gamma_notional=Decimal("84700000.000"),
        peak_gamma_strike=Decimal("545"),
        peak_gamma_value=Decimal("57475000.000"),
    )


def test_fake_gamma_aggregate_calculator_selects_peak_by_absolute_gamma() -> None:
    chain = _chain()
    exposures = FakeGammaExposureCalculator().calculate(chain)

    aggregate = FakeGammaAggregateCalculator().calculate(exposures, chain.symbol, chain.as_of)

    assert aggregate.items[0].absolute_gamma == Decimal("27225000.000")
    assert aggregate.items[1].absolute_gamma == Decimal("57475000.000")
    assert aggregate.peak_gamma_strike == Decimal("545")
    assert aggregate.peak_gamma_value == Decimal("57475000.000")


def test_calculate_gamma_aggregate_use_case_uses_gamma_exposure_output() -> None:
    class RecordingGammaAggregateCalculator:
        def __init__(self) -> None:
            self.received_symbol: str | None = None
            self.received_exposure_count = 0

        def calculate(self, exposures, symbol, as_of) -> GammaAggregate:  # noqa: ANN001
            self.received_symbol = symbol
            self.received_exposure_count = len(exposures)
            return GammaAggregate(symbol=symbol, as_of=as_of)

    calculator: IGammaAggregateCalculator = RecordingGammaAggregateCalculator()
    use_case = CalculateGammaAggregateUseCase(FakeGammaExposureCalculator(), calculator)
    chain = _chain()

    result = use_case.execute(chain)

    assert result.symbol == "SPY"
    assert calculator.received_symbol == "SPY"
    assert calculator.received_exposure_count == len(chain.contracts)


def test_legacy_gamma_aggregate_endpoint_is_removed() -> None:
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        response = client.post("/options/gamma-aggregate", json=_chain_payload())

    assert response.status_code == 404
    return
    assert response.json() == {
        "schema_version": 1,
        "symbol": "SPY",
        "as_of": "2026-01-15T14:30:00Z",
        "total_market_gamma": 84700000,
        "positive_gamma": 84700000,
        "negative_gamma": 0,
        "peak_gamma_strike": 545,
        "peak_gamma_value": 57475000,
        "items": [
            {
                "strike": 540,
                "total_gamma_exposure": 117975000,
                "call_gamma_exposure": 72600000,
                "put_gamma_exposure": -45375000,
                "net_gamma": 27225000,
                "contract_count": 2,
                "absolute_gamma": 27225000,
                "open_interest": 0,
                "volume": 0,
            },
            {
                "strike": 545,
                "total_gamma_exposure": 63525000,
                "call_gamma_exposure": 60500000,
                "put_gamma_exposure": -3025000,
                "net_gamma": 57475000,
                "contract_count": 2,
                "absolute_gamma": 57475000,
                "open_interest": 0,
                "volume": 0,
            },
        ],
    }


def _chain() -> OptionChain:
    return OptionChain(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        spot_price=Decimal("550"),
        contracts=(
            _contract(ContractType.CALL, "SPY260220C00540000", Decimal("540"), Decimal("0.030"), 8000),
            _contract(ContractType.PUT, "SPY260220P00540000", Decimal("540"), Decimal("0.025"), 6000),
            _contract(ContractType.CALL, "SPY260220C00545000", Decimal("545"), Decimal("0.040"), 5000),
            _contract(ContractType.PUT, "SPY260220P00545000", Decimal("545"), Decimal("0.010"), 1000),
        ),
    )


def _contract(contract_type: ContractType, occ_symbol: str, strike: Decimal, gamma: Decimal, open_interest: int) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        strike=strike,
        expiration=date(2026, 2, 20),
        contract_type=contract_type,
        occ_symbol=occ_symbol,
        bid=Decimal("1.20"),
        ask=Decimal("1.25"),
        last=Decimal("1.22"),
        volume=3400,
        open_interest=open_interest,
        iv=Decimal("0.18"),
        greeks=Greeks(
            delta=Decimal("0"),
            gamma=gamma,
            theta=Decimal("0"),
            vega=Decimal("0"),
            charm=Decimal("0"),
            vanna=Decimal("0"),
        ),
    )


def _chain_payload() -> dict[str, object]:
    return {
        "symbol": "SPY",
        "as_of": "2026-01-15T14:30:00Z",
        "spot_price": 550,
        "contracts": [
            {
                "occ_symbol": c.occ_symbol,
                "underlying": "SPY",
                "strike": float(c.strike),
                "expiration": "2026-02-20",
                "type": c.contract_type.value,
                "bid": 1.2,
                "ask": 1.25,
                "last": 1.22,
                "iv": 0.18,
                "delta": 0,
                "gamma": float(c.greeks.gamma),
                "theta": 0,
                "vega": 0,
                "charm": 0,
                "vanna": 0,
                "open_interest": c.open_interest,
                "volume": 3400,
            }
            for c in _chain().contracts
        ],
    }
