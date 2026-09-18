from __future__ import annotations

from decimal import Decimal

from backend.domain.entities import GammaAggregate, GammaFlip
from backend.domain.ports import IGammaFlipCalculator


class FakeGammaFlipCalculator(IGammaFlipCalculator):
    """Deterministic Gamma Flip calculator based on Gamma Aggregate net gamma.

    Picks the sign crossing closest to the current spot price, not simply
    the first one found scanning strikes from the lowest -- confirmed
    live, 2026-09-18 (AAPL): a single low-magnitude strike far from spot
    (e.g. net_gamma of a few million, next to a neighboring strike near
    spot worth hundreds of millions) could flip sign from one refresh
    cycle to the next on nothing more than IV/OI quote noise, relocating
    the reported level by double-digit points with no real market move
    (gamma_flip jumped from ~327 to 344.56 while price sat flat at
    ~335.4-335.9 across ten consecutive 30s cycles). Backtested against a
    full day of real per-strike history (AAPL, SPX, NDX) before adopting
    this: picking the nearest-to-spot crossing cut AAPL's daily range
    from ~22 points to ~17 and its >5-point jumps from 73 to 8, while
    leaving SPX and NDX -- whose naive behavior was already stable --
    completely unchanged (same values, same coverage). A magnitude
    threshold on top (requiring the crossing strike's net_gamma to be a
    minimum fraction of the book's largest strike) was evaluated and
    rejected: it also helped AAPL, but made SPX *worse* (more jumps) and
    destroyed NDX's coverage (dropped to ~1% of days), since neither
    index's book has a single dominant "whale" strike to threshold
    against the way AAPL's does. See the Gamma Flip entry in the Engine
    Interpretation Guide for the full methodology comparison against
    SpotGamma's own Zero Gamma definition and the known limitation this
    does *not* address (gamma priced once at the real current spot,
    never swept across hypothetical spot levels).
    """

    def calculate(self, aggregate: GammaAggregate, spot_price: Decimal) -> GammaFlip:
        sorted_items = tuple(sorted(aggregate.items, key=lambda item: item.strike))

        closest_flip: GammaFlip | None = None
        closest_distance: Decimal | None = None
        for lower, upper in zip(sorted_items, sorted_items[1:], strict=False):
            lower_gamma = lower.net_gamma
            upper_gamma = upper.net_gamma
            if lower_gamma == 0 or upper_gamma == 0 or lower_gamma * upper_gamma > 0:
                continue

            interpolation_ratio = -lower_gamma / (upper_gamma - lower_gamma)
            gamma_flip_price = lower.strike + interpolation_ratio * (upper.strike - lower.strike)
            distance = abs(gamma_flip_price - spot_price)
            if closest_distance is not None and distance >= closest_distance:
                continue

            closest_distance = distance
            closest_flip = GammaFlip(
                gamma_flip_price=gamma_flip_price,
                lower_strike=lower.strike,
                upper_strike=upper.strike,
                lower_gamma=lower_gamma,
                upper_gamma=upper_gamma,
                interpolation_ratio=interpolation_ratio,
                flip_found=True,
            )

        return closest_flip if closest_flip is not None else GammaFlip(flip_found=False)
