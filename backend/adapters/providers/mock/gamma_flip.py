from __future__ import annotations

from backend.domain.entities import GammaAggregate, GammaFlip
from backend.domain.ports import IGammaFlipCalculator


class FakeGammaFlipCalculator(IGammaFlipCalculator):
    """Deterministic Gamma Flip calculator based on Gamma Aggregate net gamma."""

    def calculate(self, aggregate: GammaAggregate) -> GammaFlip:
        sorted_items = tuple(sorted(aggregate.items, key=lambda item: item.strike))

        for lower, upper in zip(sorted_items, sorted_items[1:], strict=False):
            lower_gamma = lower.net_gamma
            upper_gamma = upper.net_gamma
            if lower_gamma == 0 or upper_gamma == 0 or lower_gamma * upper_gamma > 0:
                continue

            interpolation_ratio = -lower_gamma / (upper_gamma - lower_gamma)
            gamma_flip_price = lower.strike + interpolation_ratio * (upper.strike - lower.strike)
            return GammaFlip(
                gamma_flip_price=gamma_flip_price,
                lower_strike=lower.strike,
                upper_strike=upper.strike,
                lower_gamma=lower_gamma,
                upper_gamma=upper_gamma,
                interpolation_ratio=interpolation_ratio,
                flip_found=True,
            )

        return GammaFlip(flip_found=False)
