from __future__ import annotations

from backend.domain.entities import MarketSnapshot
from backend.domain.ports import IDataProvider


class GetMarketSnapshotUseCase:
    """Fetch a market snapshot through the domain market data provider port."""

    def __init__(self, market_data_provider: IDataProvider) -> None:
        self._market_data_provider = market_data_provider

    def execute(self, symbol: str) -> MarketSnapshot:
        return self._market_data_provider.get_underlying_snapshot(symbol)
