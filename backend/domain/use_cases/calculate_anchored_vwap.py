from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.domain.entities import AnchoredVwap, MarketPrice

NEW_YORK = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)


def calculate_session_open(as_of: datetime) -> datetime:
    """Return the 9:30 ET session anchor for the trading day of `as_of`."""
    local = as_of.astimezone(NEW_YORK)
    return datetime.combine(local.date(), SESSION_OPEN, NEW_YORK)


def calculate_anchored_vwap(readings: list[MarketPrice], as_of: datetime) -> AnchoredVwap:
    """Calculate the session-anchored VWAP from persisted `market_snapshots` reads.

    Approximation documented in `docs/dashboard-spec.md` section 10: Convexa only
    persists a point price every 30s (no OHLC per interval), so each interval's
    typical price is approximated as that reading's price. `volume` on each
    reading is the session-cumulative total, so an interval's traded volume is
    the delta against the prior reading; the session's first reading has no
    prior reading to diff against, so its own volume is used as-is (the
    session-volume counter resets to zero at 9:30 ET).
    """
    anchor = calculate_session_open(as_of)
    anchor_utc = anchor.astimezone(UTC)
    session_readings = sorted(
        (reading for reading in readings if anchor <= reading.as_of <= as_of),
        key=lambda reading: reading.as_of,
    )

    total_price_volume = Decimal(0)
    total_volume = 0
    previous_volume = 0
    for reading in session_readings:
        interval_volume = max(reading.volume - previous_volume, 0)
        previous_volume = reading.volume
        total_price_volume += reading.price * interval_volume
        total_volume += interval_volume

    if total_volume <= 0:
        return AnchoredVwap(
            value=None,
            provisional=True,
            anchor_time=anchor_utc,
            sample_count=len(session_readings),
        )

    return AnchoredVwap(
        value=total_price_volume / Decimal(total_volume),
        provisional=False,
        anchor_time=anchor_utc,
        sample_count=len(session_readings),
    )
