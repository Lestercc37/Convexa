from __future__ import annotations

from dataclasses import dataclass, replace

from backend.domain.entities import DerivedMetrics, GammaAggregate, MarketPrice, OptionChain
from backend.domain.ports import IDataProvider, IStorage
from backend.domain.use_cases.calculate_derived_metrics import (
    CalculateDerivedMetricsUseCase,
    capture_daily_gamma_reference,
)
from backend.domain.use_cases.flow import WhaleAlertsEngine
from backend.domain.use_cases.gamma import CalculateGammaExposureOrchestrator


def _merge_cumulative_volume(chain: OptionChain, storage: IStorage) -> OptionChain:
    """A contract's `volume` field comes from `IDataProvider.get_option_chain()`,
    which reads it straight off the provider's own live trade-stream state
    (ThetaStreamHub._cumulative_volume) -- always correct for a process that
    actually owns a live stream, but always 0 for one that doesn't (the new
    scheduler-only process, split out from backend/worker.py to stop the
    scheduler's own REST/JSON/object-construction work contending with
    ThetaStreamHub's event loop for the GIL -- confirmed live, 2026-09-22,
    that this contention was the real cause of a WebSocket reconnect storm
    ThetaData support attributed to us being a "slow consumer").

    WhaleAlertsEngine.process(chain) -- called right after this in
    execute() -- depends on real volume for its own detection; silently
    persisting/processing an all-zero-volume chain from the scheduler-only
    process would quietly degrade that, not just report a wrong number
    somewhere. Only touches contracts whose own volume is 0 -- a process
    WITH a live stream already has the real, current value and must never
    have it overwritten by a periodic, necessarily-lagged Postgres read of
    ANOTHER process's own snapshot (see core/stream_state_export.py's
    StreamStateExporter, the writer this reads)."""
    zero_volume_occ_symbols = [
        contract.occ_symbol for contract in chain.contracts if contract.volume == 0
    ]
    if not zero_volume_occ_symbols:
        return chain
    real_volumes = storage.get_cumulative_volumes(zero_volume_occ_symbols)
    if not real_volumes:
        return chain
    return replace(
        chain,
        contracts=tuple(
            replace(contract, volume=real_volumes[contract.occ_symbol])
            if contract.occ_symbol in real_volumes
            else contract
            for contract in chain.contracts
        ),
    )


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
        chain = _merge_cumulative_volume(chain, self.storage)
        self.storage.save_chain_snapshot(chain)
        self.whale_alerts_engine.process(chain)

        # Net client flow pressure (SymbolFlowPressure) used to be read
        # here, off `self.whale_alerts_engine.symbol_flow(symbol)`, and
        # saved on this method's own cadence -- moved out (2026-09-22,
        # the scheduler/stream process split) because `self.whale_alerts_
        # engine` in THIS process (scheduler_worker.py) never receives a
        # single process_trade() call any more (only backend/worker.py's
        # own, separate WhaleAlertsEngine instance does, from the real
        # trade stream) -- reading it here would always see nothing,
        # same class of bug _merge_cumulative_volume above exists to
        # avoid for chain.volume. StreamStateExporter
        # (backend/core/stream_state_export.py), run by backend/worker.py
        # (the process that DOES own the live stream and the engine
        # instance process_trade() actually feeds), persists it directly
        # from there now instead.
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

        # execute_both, not execute -- also builds and persists the
        # Tactical (0-2 DTE) GammaAggregate alongside the Structural one,
        # from the same chain/daily-bars fetch (see that method's own
        # docstring). This return value only ever carried the Structural
        # aggregate; Tactical isn't consumed synchronously anywhere in
        # this pipeline, it's read back later via the API's own
        # ?view=tactical query param -- so the return type stays
        # unchanged, Tactical is persisted purely as a side effect here.
        aggregate, _tactical = self.gamma_exposure_orchestrator.execute_both(symbol)
        # Structural only, deliberately -- capture_daily_gamma_reference
        # feeds DerivedMetrics' own historical comparisons, which stay
        # Structural-only for this feature's first version (confirmed
        # with the user, 2026-09-25: Dealer Impact Score/Signal Alignment
        # Score/Market Bias would need their own tactical history to mean
        # anything under Tactical, materially more work than this pass).
        capture_daily_gamma_reference(self.storage, aggregate, market)
        derived_metrics = self.derived_metrics_use_case.execute(symbol)
        return aggregate, derived_metrics
