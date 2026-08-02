from __future__ import annotations

from decimal import Decimal
from typing import Callable, TypeVar

from backend.domain.entities import CallWall, GammaAggregate, GammaAggregateItem, PutWall, Walls
from backend.domain.ports import IWallCalculator

WallT = TypeVar("WallT", CallWall, PutWall)


class FakeWallCalculator(IWallCalculator):
    """Deterministic institutional Call Wall / Put Wall calculator."""

    def calculate(self, aggregate: GammaAggregate) -> Walls:
        return Walls(
            symbol=aggregate.symbol,
            as_of=aggregate.as_of,
            call_wall=self._select_wall(
                aggregate.items,
                gamma_selector=lambda item: item.call_gamma_exposure,
                wall_type=CallWall,
            ),
            put_wall=self._select_wall(
                aggregate.items,
                gamma_selector=lambda item: item.put_gamma_exposure,
                wall_type=PutWall,
            ),
        )

    def _select_wall(
        self,
        items: tuple[GammaAggregateItem, ...],
        gamma_selector: Callable[[GammaAggregateItem], Decimal],
        wall_type: type[WallT],
    ) -> WallT | None:
        candidates = [(item, abs(gamma_selector(item))) for item in items if gamma_selector(item)]
        if not candidates:
            return None
        selected_item, _ = max(candidates, key=lambda candidate: candidate[1])
        return wall_type(
            strike=selected_item.strike,
            gamma=gamma_selector(selected_item),
            open_interest=selected_item.open_interest,
            volume=selected_item.volume,
        )
