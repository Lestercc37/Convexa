from __future__ import annotations

from backend.domain.entities import GammaAggregate, OptionChain
from backend.domain.ports import IGammaAggregateCalculator, IGammaExposureCalculator


class CalculateGammaAggregateUseCase:
    """Calculate Gamma Aggregate from Gamma Exposure output."""

    def __init__(
        self,
        gamma_exposure_calculator: IGammaExposureCalculator,
        gamma_aggregate_calculator: IGammaAggregateCalculator,
    ) -> None:
        self._gamma_exposure_calculator = gamma_exposure_calculator
        self._gamma_aggregate_calculator = gamma_aggregate_calculator

    def execute(self, chain: OptionChain) -> GammaAggregate:
        exposures = self._gamma_exposure_calculator.calculate(chain)
        return self._gamma_aggregate_calculator.calculate(
            exposures, chain.symbol, chain.as_of
        )
