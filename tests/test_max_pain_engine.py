from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal


from backend.adapters.providers.mock.max_pain import FakeMaxPainCalculator
from backend.domain.use_cases import CalculateMaxPainUseCase
from backend.domain.entities import (
    ContractType,
    Greeks,
    MaxPain,
    MaxPainStrikePain,
    OptionChain,
    OptionContract,
)
from backend.domain.ports import IMaxPainCalculator


def test_fake_max_pain_calculator_selects_lowest_total_pain_and_top_five() -> None:
    max_pain = FakeMaxPainCalculator().calculate(_chain())

    assert max_pain == MaxPain(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        max_pain_strike=Decimal("105"),
        total_call_pain=Decimal("2500"),
        total_put_pain=Decimal("1500"),
        total_pain=Decimal("4000"),
        ranking=(
            MaxPainStrikePain(
                Decimal("105"), Decimal("2500"), Decimal("1500"), Decimal("4000")
            ),
            MaxPainStrikePain(Decimal("100"), Decimal("0"), Decimal("6500"), Decimal("6500")),
            MaxPainStrikePain(Decimal("110"), Decimal("9000"), Decimal("0"), Decimal("9000")),
        ),
    )


def test_calculate_max_pain_use_case_uses_calculator() -> None:
    class RecordingMaxPainCalculator:
        def __init__(self) -> None:
            self.received_symbol: str | None = None

        def calculate(self, chain: OptionChain) -> MaxPain:
            self.received_symbol = chain.symbol
            return MaxPain(
                symbol=chain.symbol,
                as_of=chain.as_of,
                max_pain_strike=Decimal("100"),
                total_call_pain=Decimal("0"),
                total_put_pain=Decimal("0"),
                total_pain=Decimal("0"),
            )

    calculator: IMaxPainCalculator = RecordingMaxPainCalculator()
    result = CalculateMaxPainUseCase(calculator).execute(_chain())

    assert result.symbol == "SPY"
    assert calculator.received_symbol == "SPY"


def test_legacy_max_pain_endpoint_is_removed() -> None:
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        response = client.post("/options/max-pain", json=_chain_payload())

    assert response.status_code == 404
    return
    assert response.json() == {
        "schema_version": 1,
        "symbol": "SPY",
        "as_of": "2026-01-15T14:30:00Z",
        "max_pain_strike": 105,
        "total_call_pain": 2500,
        "total_put_pain": 1500,
        "total_pain": 4000,
        "ranking": [
            {"strike": 105, "total_call_pain": 2500, "total_put_pain": 1500, "total_pain": 4000},
            {"strike": 100, "total_call_pain": 0, "total_put_pain": 6500, "total_pain": 6500},
            {"strike": 110, "total_call_pain": 9000, "total_put_pain": 0, "total_pain": 9000},
        ],
    }


def _chain() -> OptionChain:
    return OptionChain(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        spot_price=Decimal("550"),
        contracts=tuple(
            _contract(strike, contract_type, open_interest)
            for strike, contract_type, open_interest in (
                ("100", ContractType.CALL, 500),
                ("105", ContractType.CALL, 800),
                ("110", ContractType.PUT, 300),
                ("105", ContractType.PUT, 700),
            )
        ),
    )


def _contract(strike: str, contract_type: ContractType, open_interest: int) -> OptionContract:
    suffix = "C" if contract_type == ContractType.CALL else "P"
    return OptionContract(
        underlying="SPY",
        strike=Decimal(strike),
        expiration=date(2026, 2, 20),
        contract_type=contract_type,
        occ_symbol=f"SPY260220{suffix}{int(Decimal(strike) * 1000):08d}",
        bid=Decimal("1"),
        ask=Decimal("1.1"),
        last=Decimal("1.05"),
        volume=100,
        open_interest=open_interest,
        iv=Decimal("0.2"),
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
                "occ_symbol": contract.occ_symbol,
                "underlying": contract.underlying,
                "strike": int(contract.strike),
                "expiration": contract.expiration.isoformat(),
                "type": contract.contract_type.value,
                "bid": 1,
                "ask": 1.1,
                "last": 1.05,
                "iv": 0.2,
                "delta": 0,
                "gamma": 0,
                "theta": 0,
                "vega": 0,
                "charm": 0,
                "vanna": 0,
                "open_interest": contract.open_interest,
                "volume": contract.volume,
            }
            for contract in _chain().contracts
        ],
    }
