from __future__ import annotations

from backend.domain.entities import GammaExposure, OptionChain
from backend.domain.ports import IGammaExposureCalculator


class CalculateGammaExposureUseCase:
    """Calculate per-contract Gamma Exposure by delegating to the domain port."""

    def __init__(self, gamma_exposure_calculator: IGammaExposureCalculator) -> None:
        self._gamma_exposure_calculator = gamma_exposure_calculator

    def execute(self, chain: OptionChain) -> tuple[GammaExposure, ...]:
        return self._gamma_exposure_calculator.calculate(chain)
