from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.providers.mock.fake import FakeGreeksCalculator
from backend.domain.use_cases import CalculateGreeksUseCase
from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.ports import IGreeksCalculator
from backend.main import app


def test_fake_greeks_calculator_enriches_chain_deterministically() -> None:
    chain = _chain()

    enriched = FakeGreeksCalculator().calculate(chain)

    assert enriched is not chain
    assert len(enriched.contracts) == len(chain.contracts)
    assert enriched.contracts[0].greeks.delta > Decimal("0.5")
    assert enriched.contracts[1].greeks.delta < Decimal("0")
    assert enriched.contracts[0].greeks.gamma > Decimal("0")
    assert enriched.contracts[0].greeks.theta < Decimal("0")
    assert enriched.contracts[0].greeks.vega > Decimal("0")
    assert enriched.contracts[0].greeks.charm != Decimal("0")
    assert enriched.contracts[0].greeks.vanna != Decimal("0")


def test_fake_greeks_vary_with_moneyness_and_expiration() -> None:
    chain = _chain()
    base = chain.contracts[0]
    varied_chain = replace(
        chain,
        contracts=(
            base,
            replace(
                base,
                occ_symbol="SPY260320C00560000",
                strike=Decimal("560"),
                expiration=base.expiration + timedelta(days=28),
            ),
        ),
    )

    result = FakeGreeksCalculator().calculate(varied_chain)

    assert result.contracts[0].greeks != result.contracts[1].greeks
    assert result.contracts[0].greeks.charm != result.contracts[1].greeks.charm
    assert result.contracts[0].greeks.vanna != result.contracts[1].greeks.vanna


def test_fake_greeks_generates_deterministic_charm_and_vanna() -> None:
    chain = _chain()
    calculator = FakeGreeksCalculator()

    first = calculator.calculate(chain)
    second = calculator.calculate(chain)

    assert first.contracts[0].greeks.charm == second.contracts[0].greeks.charm
    assert first.contracts[0].greeks.vanna == second.contracts[0].greeks.vanna
    assert first.contracts[0].greeks.charm != Decimal("0")
    assert first.contracts[0].greeks.vanna != Decimal("0")


def test_calculate_greeks_use_case_depends_only_on_port() -> None:
    class RecordingGreeksCalculator:
        def __init__(self) -> None:
            self.received: OptionChain | None = None

        def calculate(self, chain: OptionChain) -> OptionChain:
            self.received = chain
            return chain

    calculator: IGreeksCalculator = RecordingGreeksCalculator()
    use_case = CalculateGreeksUseCase(calculator)
    chain = _chain()

    result = use_case.execute(chain)

    assert result is chain
    assert calculator.received is chain


def test_greeks_endpoint_returns_enriched_chain() -> None:
    with TestClient(app) as client:
        response = client.post("/options/greeks", json=_chain_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "SPY"
    assert len(payload["contracts"]) == 2
    assert payload["spot_price"] == 550
    assert payload["contracts"][0]["delta"] > 0.5
    assert payload["contracts"][0]["gamma"] > 0
    assert payload["contracts"][0]["theta"] < 0
    assert payload["contracts"][0]["vega"] > 0
    assert payload["contracts"][0]["charm"] != 0
    assert payload["contracts"][0]["vanna"] != 0
    assert payload["contracts"][1]["delta"] < 0


def _chain() -> OptionChain:
    return OptionChain(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        spot_price=Decimal("550"),
        contracts=(
            _contract(ContractType.CALL, "SPY260220C00540000"),
            _contract(ContractType.PUT, "SPY260220P00540000"),
        ),
    )


def _contract(contract_type: ContractType, occ_symbol: str) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        strike=Decimal("540"),
        expiration=date(2026, 2, 20),
        contract_type=contract_type,
        occ_symbol=occ_symbol,
        bid=Decimal("1.20"),
        ask=Decimal("1.25"),
        last=Decimal("1.22"),
        volume=3400,
        open_interest=8000,
        iv=Decimal("0.18"),
        greeks=Greeks(
            delta=Decimal("0"),
            gamma=Decimal("0"),
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
                "occ_symbol": "SPY260220C00540000",
                "underlying": "SPY",
                "strike": 540,
                "expiration": "2026-02-20",
                "type": "call",
                "bid": 1.2,
                "ask": 1.25,
                "last": 1.22,
                "iv": 0.18,
                "delta": 0,
                "gamma": 0,
                "theta": 0,
                "vega": 0,
                "charm": 0,
                "vanna": 0,
                "open_interest": 8000,
                "volume": 3400,
            },
            {
                "occ_symbol": "SPY260220P00540000",
                "underlying": "SPY",
                "strike": 540,
                "expiration": "2026-02-20",
                "type": "put",
                "bid": 1.2,
                "ask": 1.25,
                "last": 1.22,
                "iv": 0.18,
                "delta": 0,
                "gamma": 0,
                "theta": 0,
                "vega": 0,
                "charm": 0,
                "vanna": 0,
                "open_interest": 8000,
                "volume": 3400,
            },
        ],
    }
