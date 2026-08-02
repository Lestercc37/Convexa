from __future__ import annotations

from backend.domain.entities import MaxPain, OptionChain
from backend.domain.ports import IMaxPainCalculator


class CalculateMaxPainUseCase:
    """Calculate institutional Max Pain from an option chain."""

    def __init__(self, max_pain_calculator: IMaxPainCalculator) -> None:
        self._max_pain_calculator = max_pain_calculator

    def execute(self, chain: OptionChain) -> MaxPain:
        return self._max_pain_calculator.calculate(chain)
