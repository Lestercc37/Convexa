from __future__ import annotations

from backend.domain.entities import GammaAggregate, Walls
from backend.domain.ports import IWallCalculator


class CalculateWallsUseCase:
    """Calculate institutional Call Wall and Put Wall from Gamma Aggregate."""

    def __init__(self, wall_calculator: IWallCalculator) -> None:
        self._wall_calculator = wall_calculator

    def execute(self, aggregate: GammaAggregate) -> Walls:
        return self._wall_calculator.calculate(aggregate)
