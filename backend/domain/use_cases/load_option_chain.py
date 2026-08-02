from __future__ import annotations

from datetime import date

from backend.domain.entities import OptionChain
from backend.domain.ports import IDataProvider


class LoadOptionChainUseCase:
    """Load an option chain through the domain market data provider port."""

    def __init__(self, market_data_provider: IDataProvider) -> None:
        self._market_data_provider = market_data_provider

    def execute(self, symbol: str, expiration: date | None = None) -> OptionChain:
        return self._market_data_provider.get_option_chain(symbol, expiration)
