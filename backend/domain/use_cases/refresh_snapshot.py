from __future__ import annotations

from dataclasses import dataclass

from backend.domain.entities import DerivedMetrics, GammaAggregate, MarketPrice
from backend.domain.ports import IDataProvider, IStorage
from backend.domain.use_cases.calculate_derived_metrics import (
    CalculateDerivedMetricsUseCase,
    capture_daily_gamma_reference,
)
from backend.domain.use_cases.flow import WhaleAlertsEngine
from backend.domain.use_cases.gamma import CalculateGammaExposureOrchestrator


@dataclass(frozen=True, slots=True)
class RefreshUnderlyingSnapshotUseCase:
    """Fetches, persists, and recalculates everything for one underlying.

    Extracted from the `POST /internal/trigger-calculation/{symbol}` route
    so its 6-step pipeline (fetch chain, persist it, feed Whale Alerts,
    fetch/persist market price and daily bars, recalculate gamma exposure
    and derived metrics) has exactly one implementation, shared by that
    manual endpoint and the periodic scheduler (`backend/core/scheduler.py`)
    — neither duplicates it, and neither calls the other.
    """

    storage: IStorage
    market_data_provider: IDataProvider
    whale_alerts_engine: WhaleAlertsEngine
    gamma_exposure_orchestrator: CalculateGammaExposureOrchestrator
    derived_metrics_use_case: CalculateDerivedMetricsUseCase

    def execute(self, symbol: str) -> tuple[GammaAggregate, DerivedMetrics]:
        chain = self.market_data_provider.get_option_chain(symbol)
        self.storage.save_chain_snapshot(chain)
        self.whale_alerts_engine.process(chain)

        market = self.market_data_provider.get_underlying_snapshot(symbol)
        self.storage.save_market_price(
            MarketPrice(
                symbol=market.symbol,
                as_of=market.as_of,
                price=market.price,
                volume=market.volume,
            )
        )
        for bar in self.market_data_provider.get_daily_bars(symbol):
            self.storage.save_daily_bar(bar)

        aggregate = self.gamma_exposure_orchestrator.execute(symbol)
        capture_daily_gamma_reference(self.storage, aggregate, market)
        derived_metrics = self.derived_metrics_use_case.execute(symbol)
        return aggregate, derived_metrics
