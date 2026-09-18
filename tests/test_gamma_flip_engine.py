from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.domain.use_cases import CalculateGammaFlipUseCase
from backend.domain.entities import GammaAggregate, GammaAggregateItem, GammaFlip
from backend.domain.ports import IGammaFlipCalculator


def test_fake_gamma_flip_calculator_detects_positive_to_negative_change() -> None:
    aggregate = _aggregate((Decimal("540"), Decimal("90")), (Decimal("545"), Decimal("-10")))

    flip = FakeGammaFlipCalculator().calculate(aggregate, Decimal("542"))

    assert flip == GammaFlip(
        gamma_flip_price=Decimal("544.5"),
        lower_strike=Decimal("540"),
        upper_strike=Decimal("545"),
        lower_gamma=Decimal("90"),
        upper_gamma=Decimal("-10"),
        interpolation_ratio=Decimal("0.9"),
        flip_found=True,
    )


def test_fake_gamma_flip_calculator_detects_negative_to_positive_change() -> None:
    aggregate = _aggregate((Decimal("540"), Decimal("-20")), (Decimal("545"), Decimal("30")))

    flip = FakeGammaFlipCalculator().calculate(aggregate, Decimal("542"))

    assert flip.gamma_flip_price == Decimal("542.0")
    assert flip.lower_gamma == Decimal("-20")
    assert flip.upper_gamma == Decimal("30")
    assert flip.interpolation_ratio == Decimal("0.4")
    assert flip.flip_found is True


def test_fake_gamma_flip_calculator_returns_not_found_without_sign_change() -> None:
    aggregate = _aggregate((Decimal("540"), Decimal("20")), (Decimal("545"), Decimal("30")))

    flip = FakeGammaFlipCalculator().calculate(aggregate, Decimal("542"))

    assert flip.flip_found is False
    assert flip.gamma_flip_price is None


def test_fake_gamma_flip_calculator_picks_the_crossing_closest_to_spot_not_the_first_found() -> None:
    # Confirmed live, 2026-09-18 (AAPL): a single low-magnitude strike far
    # from spot flipping sign on quote noise alone used to relocate the
    # reported level by double-digit points -- the crossing near strike
    # 540 here is tiny (dwarfed by the crossing near 560, right where
    # spot actually is) and must lose to it, even though it's the first
    # one found scanning strikes from the lowest.
    aggregate = _aggregate(
        (Decimal("535"), Decimal("50")),
        (Decimal("540"), Decimal("-1")),  # noisy, low-magnitude crossing far from spot
        (Decimal("545"), Decimal("2")),
        (Decimal("555"), Decimal("400")),
        (Decimal("560"), Decimal("-350")),  # real, high-magnitude crossing right at spot
        (Decimal("565"), Decimal("-300")),
    )

    flip = FakeGammaFlipCalculator().calculate(aggregate, Decimal("558"))

    assert flip.flip_found is True
    assert flip.lower_strike == Decimal("555")
    assert flip.upper_strike == Decimal("560")


def test_fake_gamma_flip_calculator_still_returns_the_only_crossing_far_from_spot() -> None:
    # Nearest-to-spot only changes which crossing wins when there's more
    # than one -- a lone real crossing, however far from spot, is still
    # reported rather than discarded (no magnitude/distance threshold).
    aggregate = _aggregate((Decimal("540"), Decimal("90")), (Decimal("545"), Decimal("-10")))

    flip = FakeGammaFlipCalculator().calculate(aggregate, Decimal("600"))

    assert flip.flip_found is True
    assert flip.gamma_flip_price == Decimal("544.5")


def test_calculate_gamma_flip_use_case_uses_gamma_aggregate_input() -> None:
    class RecordingGammaFlipCalculator:
        def __init__(self) -> None:
            self.received: GammaAggregate | None = None
            self.received_spot_price: Decimal | None = None

        def calculate(self, aggregate: GammaAggregate, spot_price: Decimal) -> GammaFlip:
            self.received = aggregate
            self.received_spot_price = spot_price
            return GammaFlip(flip_found=False)

    calculator: IGammaFlipCalculator = RecordingGammaFlipCalculator()
    use_case = CalculateGammaFlipUseCase(calculator)
    aggregate = _aggregate((Decimal("540"), Decimal("20")))

    result = use_case.execute(aggregate, Decimal("542"))

    assert result.flip_found is False
    assert calculator.received is aggregate
    assert calculator.received_spot_price == Decimal("542")


def test_legacy_gamma_flip_endpoint_is_removed() -> None:
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        response = client.post("/options/gamma-flip", json=_aggregate_payload(90, -10))

    assert response.status_code == 404
    return
    assert response.json() == {
        "schema_version": 1,
        "gamma_flip_price": 544.5,
        "lower_strike": 540,
        "upper_strike": 545,
        "lower_gamma": 90,
        "upper_gamma": -10,
        "interpolation_ratio": 0.9,
        "flip_found": True,
    }


def test_legacy_gamma_flip_endpoint_stays_removed_for_all_payloads() -> None:
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        response = client.post("/options/gamma-flip", json=_aggregate_payload(20, 30))

    assert response.status_code == 404
    return
    assert response.json()["flip_found"] is False
    assert response.json()["gamma_flip_price"] is None


def _aggregate(*items: tuple[Decimal, Decimal]) -> GammaAggregate:
    return GammaAggregate(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        items=tuple(
            GammaAggregateItem(
                strike=strike,
                total_gamma_exposure=abs(net_gamma),
                call_gamma_exposure=max(net_gamma, Decimal("0")),
                put_gamma_exposure=min(net_gamma, Decimal("0")),
                net_gamma=net_gamma,
                contract_count=1,
                absolute_gamma=abs(net_gamma),
            )
            for strike, net_gamma in items
        ),
    )


def _aggregate_payload(lower_gamma: int, upper_gamma: int) -> dict[str, object]:
    return {
        "symbol": "SPY",
        "as_of": "2026-01-15T14:30:00Z",
        "items": [
            _item_payload(540, lower_gamma),
            _item_payload(545, upper_gamma),
        ],
    }


def _item_payload(strike: int, net_gamma: int) -> dict[str, object]:
    return {
        "strike": strike,
        "total_gamma_exposure": abs(net_gamma),
        "call_gamma_exposure": max(net_gamma, 0),
        "put_gamma_exposure": min(net_gamma, 0),
        "net_gamma": net_gamma,
        "contract_count": 1,
        "absolute_gamma": abs(net_gamma),
    }
