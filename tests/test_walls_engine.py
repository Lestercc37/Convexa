from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backend.adapters.providers.mock.walls import FakeWallCalculator
from backend.domain.use_cases import CalculateWallsUseCase
from backend.domain.entities import CallWall, GammaAggregate, GammaAggregateItem, PutWall, Walls
from backend.domain.ports import IWallCalculator


def test_fake_wall_calculator_selects_largest_call_and_put_gamma_by_magnitude() -> None:
    aggregate = _aggregate()

    walls = FakeWallCalculator().calculate(aggregate)

    assert walls == Walls(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        call_wall=CallWall(
            strike=Decimal("540"),
            gamma=Decimal("240"),
            open_interest=14000,
            volume=6800,
        ),
        put_wall=PutWall(
            strike=Decimal("540"),
            gamma=Decimal("-150"),
            open_interest=14000,
            volume=6800,
        ),
    )


def test_calculate_walls_use_case_uses_wall_calculator() -> None:
    class RecordingWallCalculator:
        def __init__(self) -> None:
            self.received_symbol: str | None = None

        def calculate(self, aggregate: GammaAggregate) -> Walls:
            self.received_symbol = aggregate.symbol
            return Walls(symbol=aggregate.symbol, as_of=aggregate.as_of)

    calculator: IWallCalculator = RecordingWallCalculator()
    use_case = CalculateWallsUseCase(calculator)

    result = use_case.execute(_aggregate())

    assert result.symbol == "SPY"
    assert calculator.received_symbol == "SPY"


def test_walls_endpoint_returns_response_model() -> None:
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        response = client.post("/options/walls", json=_aggregate_payload())

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "symbol": "SPY",
        "as_of": "2026-01-15T14:30:00Z",
        "call_wall": {
            "strike": 540,
            "gamma": 240,
            "open_interest": 14000,
            "volume": 6800,
        },
        "put_wall": {
            "strike": 540,
            "gamma": -150,
            "open_interest": 14000,
            "volume": 6800,
        },
    }


def _aggregate() -> GammaAggregate:
    return GammaAggregate(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        items=(
            GammaAggregateItem(
                strike=Decimal("540"),
                total_gamma_exposure=Decimal("390"),
                call_gamma_exposure=Decimal("240"),
                put_gamma_exposure=Decimal("-150"),
                net_gamma=Decimal("90"),
                contract_count=2,
                absolute_gamma=Decimal("90"),
                open_interest=14000,
                volume=6800,
            ),
            GammaAggregateItem(
                strike=Decimal("545"),
                total_gamma_exposure=Decimal("210"),
                call_gamma_exposure=Decimal("200"),
                put_gamma_exposure=Decimal("-10"),
                net_gamma=Decimal("190"),
                contract_count=2,
                absolute_gamma=Decimal("190"),
                open_interest=6000,
                volume=4200,
            ),
        ),
    )


def _aggregate_payload() -> dict[str, object]:
    return {
        "symbol": "SPY",
        "as_of": "2026-01-15T14:30:00Z",
        "items": [
            {
                "strike": 540,
                "total_gamma_exposure": 390,
                "call_gamma_exposure": 240,
                "put_gamma_exposure": -150,
                "net_gamma": 90,
                "contract_count": 2,
                "absolute_gamma": 90,
                "open_interest": 14000,
                "volume": 6800,
            },
            {
                "strike": 545,
                "total_gamma_exposure": 210,
                "call_gamma_exposure": 200,
                "put_gamma_exposure": -10,
                "net_gamma": 190,
                "contract_count": 2,
                "absolute_gamma": 190,
                "open_interest": 6000,
                "volume": 4200,
            },
        ],
    }
