from decimal import Decimal

from backend.adapters.providers.mock import MockDataProvider
from backend.domain.use_cases import GetMarketSnapshotUseCase


def test_get_market_snapshot_use_case_returns_valid_snapshot() -> None:
    use_case = GetMarketSnapshotUseCase(MockDataProvider())

    snapshot = use_case.execute("spy")

    assert snapshot.symbol == "SPY"
    assert snapshot.price == Decimal("552.25")
    assert snapshot.volume == 1_250_000
