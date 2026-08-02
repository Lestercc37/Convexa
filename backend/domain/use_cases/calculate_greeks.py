from __future__ import annotations

from backend.domain.entities import OptionChain
from backend.domain.ports import IGreeksCalculator


class CalculateGreeksUseCase:
    """Enrich an option chain by delegating Greeks calculation to the domain port."""

    def __init__(self, greeks_calculator: IGreeksCalculator) -> None:
        self._greeks_calculator = greeks_calculator

    def execute(self, chain: OptionChain) -> OptionChain:
        return self._greeks_calculator.calculate(chain)
