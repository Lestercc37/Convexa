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

        # Net client flow pressure (SymbolFlowPressure) lives only in
        # WhaleAlertsEngine's in-memory session accumulation, fed by
        # process_trade() (the real trade stream) -- see that method's
        # own comment for why process() above doesn't also feed it.
        # Snapshotting it to storage here, on the same ~30s cadence as
        # everything else this method persists, is what makes it visible
        # to the API process at all: since the process split, the API's
        # own WhaleAlertsEngine instance never receives any
        # process()/process_trade() calls (it doesn't run the streams),
        # so reading the Worker's in-memory engine directly from an API
        # route would always see nothing. A plain sync write, not
        # debounced per-trade -- this whole method already runs off the
        # event loop (asyncio.to_thread, see core/scheduler.py), so
        # there's no risk of blocking it the way an ungated per-trade
        # write would (that mistake already happened once, and was fixed,
        # for StreamUnderlyingPriceUseCase's own MarketPrice writes).
        flow_pressure = self.whale_alerts_engine.symbol_flow(symbol)
        if flow_pressure is not None:
            self.storage.save_symbol_flow_pressure(flow_pressure)

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
