from __future__ import annotations

from decimal import Decimal

from backend.domain.entities import DailyBar
from backend.domain.use_cases.calculate_atr_range import calculate_atr_range

# Confirmed with the user before implementing (docs/use-cases.md has the
# full investigation): width = ATR(14d) x 1.5, replacing both the old
# fixed strike count (n=1, inherited unreviewed from the FlashAlpha era's
# request-quota days) and any idea of a hand-tuned per-symbol percentage
# table. ATR already reflects each symbol's real volatility automatically.
ATR_WIDTH_MULTIPLIER = Decimal("1.5")

# Gamma Flip needs to search wherever dealer net gamma actually crosses
# zero, which is NOT bounded by the same "near enough to matter for Call
# Wall/Put Wall" distance the 1.5x width was tuned for -- confirmed live
# 2026-09-21: SPX's real near-term sign crossing, when found without the
# tight width, sat ~$175 from spot (outside even the current ~$98 width).
# Reuses the identical ATR-based formula, just a wider multiplier, so it
# inherits the same per-symbol-scale-aware sizing (this also incidentally
# fixes NDX, whose own 1.5x width -- ~$512 -- already silently exceeded
# the fetch's flat overfetch bound before this change) instead of a
# second hand-tuned table.
GAMMA_FLIP_WIDTH_MULTIPLIER = Decimal(4)

# Fixed, deliberately modest widths -- two distinct reasons, not one
# generic "weird symbol" exception:
FIXED_WIDTH_BY_SYMBOL: dict[str, Decimal] = {
    # VIX mean-reverts around a baseline punctuated by sharp regime-shift
    # spikes (15 -> 40 in a day during stress) -- a trailing 14-day ATR
    # is a poor sizing signal for it specifically: too narrow right
    # before a spike, or artificially wide for weeks after one, as the
    # elevated True Range values from the spike drag the average up even
    # after VIX itself has calmed back down. Lester doesn't trade VIX
    # directly, only watches it as an NQ sentiment reference, so
    # precision here isn't worth chasing.
    "VIX": Decimal(6),
    # ES has no daily-bar history to derive an ATR from at all, ever --
    # ThetaDataProvider.get_daily_bars() returns [] unconditionally for
    # futures (no working ThetaData futures EOD endpoint exists, see
    # that method's own comment). Fixed width matched to the same order
    # of magnitude as SPX, since ES tracks the S&P 500 in index points.
    "ES": Decimal(100),
}

# Defensive fallback only -- not expected to trigger for any of the 11
# symbols this project tracks today (all have well over 15 trading days
# of daily-bar history via ThetaData). Exists so a future symbol with
# too little trading history, or a transient data gap, degrades to a
# width scaled to its own price level instead of crashing or silently
# reusing a width sized for a completely different symbol.
INSUFFICIENT_DATA_WIDTH_FRACTION = Decimal("0.02")


def calculate_near_the_money_width(
    symbol: str,
    daily_bars: list[DailyBar],
    spot_price: Decimal,
    multiplier: Decimal = ATR_WIDTH_MULTIPLIER,
) -> Decimal:
    """Price-distance half-width for near-the-money strike selection.

    A contract is "near-the-money" when its strike falls within
    `spot_price` +/- the returned width. Used to filter an over-fetched,
    generously wide strike range (see NEAR_THE_MONEY_OVERFETCH_STRIKE_RANGE
    in the ThetaData adapter) down to the contracts actually worth
    streaming/computing for, without hand-tuning a width per symbol.

    `multiplier` defaults to `ATR_WIDTH_MULTIPLIER` (every existing
    caller's unchanged behavior) -- pass `GAMMA_FLIP_WIDTH_MULTIPLIER`
    for the wider search Gamma Flip needs (see that constant's own
    comment). A fixed-width symbol's own width scales by the same ratio
    (`multiplier / ATR_WIDTH_MULTIPLIER`) instead of ignoring the
    parameter, so a wider multiplier widens every symbol consistently,
    fixed or ATR-based.

    VIX and ES use a fixed base width (see FIXED_WIDTH_BY_SYMBOL for each
    one's own reason). Every other symbol gets `ATR(14d) x multiplier`,
    computed from `daily_bars` alone (`calculate_atr_range`'s own `atr`
    field does not depend on `session_readings` -- confirmed by reading
    that function, not assumed -- so this works even before the first
    live option-chain fetch of the day, seeded from `get_daily_bars()`
    alone).
    """
    fixed = FIXED_WIDTH_BY_SYMBOL.get(symbol.upper())
    if fixed is not None:
        return fixed * (multiplier / ATR_WIDTH_MULTIPLIER)

    atr_range = calculate_atr_range(daily_bars, [])
    if atr_range.atr is None:
        return spot_price * INSUFFICIENT_DATA_WIDTH_FRACTION * (multiplier / ATR_WIDTH_MULTIPLIER)
    return atr_range.atr * multiplier
