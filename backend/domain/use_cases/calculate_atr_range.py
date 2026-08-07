from __future__ import annotations

from decimal import Decimal
from itertools import pairwise

from backend.domain.entities import AtrRange, DailyBar, MarketPrice

ATR_WINDOW_DAYS = 14
REQUIRED_DAILY_BARS = ATR_WINDOW_DAYS + 1  # each True Range needs the prior day's close


def calculate_atr_range(
    daily_bars: list[DailyBar], session_readings: list[MarketPrice]
) -> AtrRange:
    """Calculate the ATR-anchored price band for the current session's open.

    `daily_bars` holds only *closed* trading days (see `DailyBar`), so True
    Range/ATR never needs intraday data. Computing `ATR_WINDOW_DAYS` (14)
    True Range values requires `REQUIRED_DAILY_BARS` (15) consecutive closed
    days — each day's True Range references the prior day's close, so the
    oldest bar in the window only supplies that reference and yields no True
    Range value of its own.

    Today's open, which anchors the bands, is *not* read from `daily_bars`
    (today's session is never persisted there — see `DailyBar`). It is the
    earliest reading in `session_readings`, the same `market_snapshots` read
    `calculate_anchored_vwap` already uses to anchor VWAP at 9:30 ET — no new
    intraday mechanism.

    The two provisional signals are independent: `atr_provisional` reflects
    only `daily_bars` history depth; `bands_provisional` also requires a
    `session_readings` entry for today, since the bands need `today_open`.
    """
    ordered_bars = sorted(daily_bars, key=lambda bar: bar.date)
    recent_bars = ordered_bars[-REQUIRED_DAILY_BARS:]

    atr: Decimal | None
    atr_provisional: bool
    if len(recent_bars) < REQUIRED_DAILY_BARS:
        atr = None
        atr_provisional = True
    else:
        true_ranges = [
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
            for previous, current in pairwise(recent_bars)
        ]
        atr = sum(true_ranges, Decimal(0)) / Decimal(len(true_ranges))
        atr_provisional = False

    today_open = (
        min(session_readings, key=lambda reading: reading.as_of).price
        if session_readings
        else None
    )

    if atr is None or today_open is None:
        return AtrRange(
            atr=atr,
            atr_provisional=atr_provisional,
            daily_bars_count=len(ordered_bars),
            today_open=today_open,
            bands_provisional=True,
            outer_upper_band=None,
            outer_lower_band=None,
            inner_upper_band=None,
            inner_lower_band=None,
        )

    half_atr = atr / 2
    return AtrRange(
        atr=atr,
        atr_provisional=False,
        daily_bars_count=len(ordered_bars),
        today_open=today_open,
        bands_provisional=False,
        outer_upper_band=today_open + atr,
        outer_lower_band=today_open - atr,
        inner_upper_band=today_open + half_atr,
        inner_lower_band=today_open - half_atr,
    )
