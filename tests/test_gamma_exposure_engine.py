from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.domain.use_cases import CalculateGammaExposureUseCase
from backend.domain.entities import ContractType, GammaExposure, Greeks, OptionChain, OptionContract
from backend.domain.ports import IGammaExposureCalculator
from backend.main import app


def test_fake_gamma_exposure_calculator_returns_one_result_per_contract_deterministically() -> None:
    chain = _chain()
    calculator = FakeGammaExposureCalculator()

    first = calculator.calculate(chain)
    second = calculator.calculate(chain)

    assert first == second
    assert len(first) == len(chain.contracts)
    assert first == (
        GammaExposure(
            occ_symbol="SPY260220C00540000",
            strike=Decimal("540"),
            contract_type=ContractType.CALL,
            expiration=date(2026, 2, 20),
            gamma=Decimal("0.030"),
            open_interest=8000,
            dealer_gamma_exposure=Decimal("72600000.000"),
            sign=Decimal("1"),
        ),
        GammaExposure(
            occ_symbol="SPY260220P00540000",
            strike=Decimal("540"),
            contract_type=ContractType.PUT,
            expiration=date(2026, 2, 20),
            gamma=Decimal("0.025"),
            open_interest=6000,
            dealer_gamma_exposure=Decimal("-45375000.000"),
            sign=Decimal("-1"),
        ),
    )


def test_calculate_gamma_exposure_use_case_depends_only_on_port() -> None:
    class RecordingGammaExposureCalculator:
        def __init__(self) -> None:
            self.received: OptionChain | None = None

        def calculate(self, chain: OptionChain) -> tuple[GammaExposure, ...]:
            self.received = chain
            return ()

    calculator: IGammaExposureCalculator = RecordingGammaExposureCalculator()
    use_case = CalculateGammaExposureUseCase(calculator)
    chain = _chain()

    result = use_case.execute(chain)

    assert result == ()
    assert calculator.received is chain


def test_gamma_exposure_endpoint_returns_per_contract_exposures() -> None:
    with TestClient(app) as client:
        response = client.post("/options/gamma-exposure", json=_chain_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert len(payload["items"]) == 2
    assert payload["items"] == [
        {
            "occ_symbol": "SPY260220C00540000",
            "strike": 540,
            "contract_type": "call",
            "expiration": "2026-02-20",
            "gamma": 0.03,
            "open_interest": 8000,
            "dealer_gamma_exposure": 72600000,
            "sign": 1,
        },
        {
            "occ_symbol": "SPY260220P00540000",
            "strike": 540,
            "contract_type": "put",
            "expiration": "2026-02-20",
            "gamma": 0.025,
            "open_interest": 6000,
            "dealer_gamma_exposure": -45375000,
            "sign": -1,
        },
    ]


def _chain() -> OptionChain:
    return OptionChain(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        spot_price=Decimal("550"),
        contracts=(
            _contract(ContractType.CALL, "SPY260220C00540000", Decimal("0.030"), 8000),
            _contract(ContractType.PUT, "SPY260220P00540000", Decimal("0.025"), 6000),
        ),
    )


def _contract(
    contract_type: ContractType,
    occ_symbol: str,
    gamma: Decimal,
    open_interest: int,
) -> OptionContract:
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
                "gamma": 0.03,
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
                "gamma": 0.025,
                "theta": 0,
                "vega": 0,
                "charm": 0,
                "vanna": 0,
                "open_interest": 6000,
                "volume": 3400,
            },
        ],
    }
