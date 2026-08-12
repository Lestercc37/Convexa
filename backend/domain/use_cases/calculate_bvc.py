from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from decimal import Decimal

# Easley, D., López de Prado, M., O'Hara, M. (2012). "Flow Toxicity and
# Liquidity in a High-Frequency World." Review of Financial Studies,
# 25(5), 1457-1493. Bulk Volume Classification: splits a period's traded
# volume into estimated buy/sell portions from price change alone, via
# Z = ΔP / σ and the standard normal CDF Φ(Z) as the estimated buy
# fraction — not a measurement of actual buy/sell-side order flow.

NEUTRAL_FRACTION = Decimal("0.5")


def calculate_price_volatility(price_deltas: Sequence[Decimal]) -> Decimal:
    """Population standard deviation of a rolling window of price deltas.

    Population (not sample/N-1) so a single-point window returns exactly
    zero rather than raising — the same degenerate case `calculate_bvc_split`
    already has to handle for an empty window, so this avoids a second,
    inconsistent edge case for one point.
    """
    if not price_deltas:
        return Decimal(0)
    return statistics.pstdev(price_deltas)


def standard_normal_cdf(z: Decimal) -> Decimal:
    """Φ(Z), the standard normal cumulative distribution function.

    `math.erf` has no Decimal equivalent in the standard library, so this
    round-trips through float — Φ(z) = 0.5 * (1 + erf(z / sqrt(2))) is the
    exact closed-form relationship (not an approximation of erf itself),
    and scipy isn't a dependency anywhere else in this project.
    """
    return Decimal(str(0.5 * (1 + math.erf(float(z) / math.sqrt(2)))))


def calculate_bvc_split(
    price_delta: Decimal,
    sigma: Decimal,
    volume: Decimal,
) -> tuple[Decimal, Decimal]:
    """Bulk Volume Classification: estimate a period's buy/sell volume split.

    Returns (estimated_buy_volume, estimated_sell_volume) — an estimate
    derived from price movement alone, never a measurement of confirmed
    buy/sell-side volume.

    `sigma == 0` (no price variance yet, or an insufficiently warmed-up
    window) falls back to a neutral 50/50 split — same "documented
    midpoint for a degenerate window" convention `calculate_iv_rank`
    already uses for a flat IV window.
    """
    if sigma == 0:
        buy_fraction = NEUTRAL_FRACTION
    else:
        z = price_delta / sigma
        buy_fraction = standard_normal_cdf(z)
    return volume * buy_fraction, volume * (Decimal(1) - buy_fraction)
