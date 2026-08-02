from __future__ import annotations

from backend.domain.entities import GammaAggregate, GammaFlip
from backend.domain.ports import IGammaFlipCalculator


class CalculateGammaFlipUseCase:
    """Calculate the Gamma Flip level from Gamma Aggregate output."""

    def __init__(self, gamma_flip_calculator: IGammaFlipCalculator) -> None:
        self._gamma_flip_calculator = gamma_flip_calculator

    def execute(self, aggregate: GammaAggregate) -> GammaFlip:
        return self._gamma_flip_calculator.calculate(aggregate)
