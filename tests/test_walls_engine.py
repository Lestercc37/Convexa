from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from backend.adapters.providers.mock.walls import FakeWallCalculator
from backend.domain.entities import CallWall, GammaAggregate, GammaAggregateItem, PutWall, Walls
from backend.domain.ports import IWallCalculator
from backend.domain.use_cases import CalculateWallsUseCase


def test_fake_wall_calculator_selects_largest_positive_and_negative_net_gamma() -> None:
    aggregate = _aggregate()

    walls = FakeWallCalculator().calculate(aggregate)

    # Both strikes in _aggregate() have positive net_gamma (540: 90,
    # 545: 190), so the call wall picks the larger one (545) and there is
    # no negative-net_gamma candidate left for the put wall -- exactly the
    # case a real chain hits when every nearby strike nets long gamma.
    assert walls == Walls(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        call_wall=CallWall(
            strike=Decimal(545),
            gamma=Decimal(190),
            open_interest=6000,
            volume=4200,
        ),
        put_wall=None,
    )


def test_fake_wall_calculator_never_selects_the_same_strike_for_both_walls() -> None:
    # Same strike, large open interest on both legs (a straddle) -- the
    # previous per-leg-magnitude approach picked this strike for BOTH
    # walls, since a call and a put at the same strike/expiry have equal
    # BSM gamma (put-call parity) and this strike has by far the largest
    # OI on each leg individually. net_gamma-based selection must still
    # find the real (smaller, but correctly-signed) walls elsewhere.
    aggregate = GammaAggregate(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        items=(
            GammaAggregateItem(
                strike=Decimal(540),
                total_gamma_exposure=Decimal(400),
                call_gamma_exposure=Decimal(200),
                put_gamma_exposure=Decimal(-200),
                net_gamma=Decimal(0),
                contract_count=2,
                absolute_gamma=Decimal(0),
                open_interest=50000,
                volume=30000,
            ),
            GammaAggregateItem(
                strike=Decimal(545),
                total_gamma_exposure=Decimal(50),
                call_gamma_exposure=Decimal(50),
                put_gamma_exposure=Decimal(0),
                net_gamma=Decimal(50),
                contract_count=1,
                absolute_gamma=Decimal(50),
                open_interest=2000,
                volume=900,
            ),
            GammaAggregateItem(
                strike=Decimal(535),
                total_gamma_exposure=Decimal(30),
                call_gamma_exposure=Decimal(0),
                put_gamma_exposure=Decimal(-30),
                net_gamma=Decimal(-30),
                contract_count=1,
                absolute_gamma=Decimal(30),
                open_interest=1500,
                volume=700,
            ),
        ),
    )

    walls = FakeWallCalculator().calculate(aggregate)

    assert walls.call_wall == CallWall(
        strike=Decimal(545), gamma=Decimal(50), open_interest=2000, volume=900
    )
    assert walls.put_wall == PutWall(
        strike=Decimal(535), gamma=Decimal(-30), open_interest=1500, volume=700
    )
    assert walls.call_wall.strike != walls.put_wall.strike


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


def test_legacy_walls_endpoint_is_removed() -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        response = client.post("/options/walls", json=_aggregate_payload())

    assert response.status_code == 404
    return
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
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        items=(
            GammaAggregateItem(
                strike=Decimal(540),
                total_gamma_exposure=Decimal(390),
                call_gamma_exposure=Decimal(240),
                put_gamma_exposure=Decimal(-150),
                net_gamma=Decimal(90),
                contract_count=2,
                absolute_gamma=Decimal(90),
                open_interest=14000,
                volume=6800,
            ),
            GammaAggregateItem(
                strike=Decimal(545),
                total_gamma_exposure=Decimal(210),
                call_gamma_exposure=Decimal(200),
                put_gamma_exposure=Decimal(-10),
                net_gamma=Decimal(190),
                contract_count=2,
                absolute_gamma=Decimal(190),
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
