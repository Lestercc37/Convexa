from __future__ import annotations

from typing import Callable, TypeVar

from backend.domain.entities import CallWall, GammaAggregate, GammaAggregateItem, PutWall, Walls
from backend.domain.ports import IWallCalculator

WallT = TypeVar("WallT", CallWall, PutWall)


class FakeWallCalculator(IWallCalculator):
    """Deterministic institutional Call Wall / Put Wall calculator.

    Selects each wall from net_gamma (call + put netted per strike), not
    each leg's raw exposure magnitude in isolation. The previous approach
    picked the strike with the single largest |call_gamma_exposure| for
    the call wall and, completely independently, the strike with the
    largest |put_gamma_exposure| for the put wall -- with nothing
    requiring the two picks to differ. Near the money a call and a put at
    the same strike/expiry have equal Black-Scholes gamma (put-call
    parity), so a heavily-traded ATM strike (large open interest on both
    legs, e.g. a straddle) routinely won *both* selections outright.
    Confirmed live, 2026-09-24: call_wall == put_wall in 6-47% of samples
    across every symbol checked (SPX, SPY, QQQ, NDX, ES, AAPL, MSFT,
    TSLA, META, AMZN, VIX, GOOGL, NVDA over the trailing 5 days; only
    IWM/DIA never collided), something the reference platform we compare
    against never does -- its two walls are always distinct strikes.

    net_gamma-based selection (call wall = the largest *positive*
    net_gamma strike, put wall = the largest-magnitude *negative*
    net_gamma strike) mirrors the same polarity split
    FakeGammaFlipCalculator already uses to find the zero-gamma crossing,
    and structurally cannot collide: a single strike's net_gamma has one
    sign, so it can never win both selections at once.
    """

    def calculate(self, aggregate: GammaAggregate) -> Walls:
        return Walls(
            symbol=aggregate.symbol,
            as_of=aggregate.as_of,
            call_wall=self._select_wall(
                aggregate.items,
                predicate=lambda item: item.net_gamma > 0,
                wall_type=CallWall,
            ),
            put_wall=self._select_wall(
                aggregate.items,
                predicate=lambda item: item.net_gamma < 0,
                wall_type=PutWall,
            ),
        )

    def _select_wall(
        self,
        items: tuple[GammaAggregateItem, ...],
        predicate: Callable[[GammaAggregateItem], bool],
        wall_type: type[WallT],
    ) -> WallT | None:
        candidates = [item for item in items if predicate(item)]
        if not candidates:
            return None
        selected_item = max(candidates, key=lambda item: abs(item.net_gamma))
        return wall_type(
            strike=selected_item.strike,
            gamma=selected_item.net_gamma,
            open_interest=selected_item.open_interest,
            volume=selected_item.volume,
        )
