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


def calculate_anchored_vwap_series(
    readings: list[MarketPrice], as_of: datetime
) -> list[tuple[datetime, Decimal]]:
    """One (timestamp, vwap) pair per session reading, running
    cumulatively from the session open through that reading -- the one
    real implementation of the VWAP math in this codebase.
    `calculate_anchored_vwap` below is just "the last point of this
    series", so a caller that wants the whole line (e.g. to seed a
    chart on mount, the same way price history already seeds candles)
    and a caller that wants only the current value both go through the
    exact same formula, never two parallel ones.

    Approximation documented in `docs/dashboard-spec.md` section 10: Convexa only
    persists a point price every 30s (no OHLC per interval), so each interval's
    typical price is approximated as that reading's price. `volume` on each
    reading is the session-cumulative total, so an interval's traded volume is
    the delta against the prior reading; the session's first reading has no
    prior reading to diff against, so its own volume is used as-is (the
    session-volume counter resets to zero at 9:30 ET).

    A reading contributes no point to the series until volume-weighting
    is actually possible (`total_volume > 0` so far) -- never a
    fabricated 0 or a repeated stale value.
    """
    anchor = calculate_session_open(as_of)
    session_readings = sorted(
        (reading for reading in readings if anchor <= reading.as_of <= as_of),
        key=lambda reading: reading.as_of,
    )

    series: list[tuple[datetime, Decimal]] = []
    total_price_volume = Decimal(0)
    total_volume = 0
    previous_volume = 0
    for reading in session_readings:
        interval_volume = max(reading.volume - previous_volume, 0)
        previous_volume = reading.volume
        total_price_volume += reading.price * interval_volume
        total_volume += interval_volume
        if total_volume > 0:
            series.append((reading.as_of, total_price_volume / Decimal(total_volume)))
    return series


def calculate_proxy_anchored_vwap_series(
    index_readings: list[MarketPrice],
    proxy_readings: list[MarketPrice],
    as_of: datetime,
) -> list[tuple[datetime, Decimal]]:
    """Anchored VWAP for a pure index (SPX/NDX -- ThetaData always reports
    volume=0 for these, see AnchoredVwap's own docstring) via a liquid,
    highly-correlated proxy ETF's real volume: SPY for SPX, QQQ for NDX.
    Real technique traders already use, not invented here -- confirmed
    with the user, 2026-09-21.

    The proxy's VWAP is expressed as a ratio to ITS OWN session-open price
    (how far volume-weighted trading has drifted from where the proxy
    opened, in percentage terms), then that same ratio is applied to the
    index's OWN session-open price. Correct despite the two trading at
    completely different absolute scales (SPY ~1/10th of SPX by design)
    because an index-tracking ETF is constructed to move in lockstep,
    proportionally, with its index -- the ratio is scale-free, only the
    two open prices anchor it back into the index's own units.

    One point per PROXY reading (never the index's own -- the proxy is
    what's actually volume-weighted here), timestamped and priced in the
    INDEX's own scale. Empty if either side has no reading yet this
    session, or if the proxy's own open price is somehow zero (never
    fabricates a ratio from a zero denominator).
    """
    anchor = calculate_session_open(as_of)
    index_session_readings = sorted(
        (reading for reading in index_readings if anchor <= reading.as_of <= as_of),
        key=lambda reading: reading.as_of,
    )
    if not index_session_readings:
        return []
    index_open_price = index_session_readings[0].price

    proxy_session_readings = sorted(
        (reading for reading in proxy_readings if anchor <= reading.as_of <= as_of),
        key=lambda reading: reading.as_of,
    )
    if not proxy_session_readings:
        return []
    proxy_open_price = proxy_session_readings[0].price
    if proxy_open_price == 0:
        return []

    proxy_series = calculate_anchored_vwap_series(proxy_readings, as_of)
    return [
        (timestamp, index_open_price * (proxy_vwap / proxy_open_price))
        for timestamp, proxy_vwap in proxy_series
    ]


def calculate_proxy_anchored_vwap(
    index_readings: list[MarketPrice],
    proxy_readings: list[MarketPrice],
    as_of: datetime,
    proxy_symbol: str,
) -> AnchoredVwap:
    """Same shape as `calculate_anchored_vwap`, sourced from
    `calculate_proxy_anchored_vwap_series` instead -- see that function's
    own docstring. `provisional=True` (never `not_applicable`) when
    nothing's computable yet this session: unlike a pure index with no
    proxy at all, this genuinely could still show up once both sides have
    a reading, so it gets the same "still accumulating" treatment a real
    equity/ETF's own VWAP does.
    """
    anchor_utc = calculate_session_open(as_of).astimezone(UTC)
    series = calculate_proxy_anchored_vwap_series(index_readings, proxy_readings, as_of)
    sample_count = len(
        [reading for reading in proxy_readings if anchor_utc <= reading.as_of <= as_of]
    )
    if not series:
        return AnchoredVwap(
            value=None,
            provisional=True,
            anchor_time=anchor_utc,
            sample_count=sample_count,
            proxy_symbol=proxy_symbol,
        )
    return AnchoredVwap(
        value=series[-1][1],
        provisional=False,
        anchor_time=anchor_utc,
        sample_count=sample_count,
        proxy_symbol=proxy_symbol,
    )


def calculate_anchored_vwap(
    readings: list[MarketPrice], as_of: datetime, not_applicable: bool = False
) -> AnchoredVwap:
    """Calculate the session-anchored VWAP from persisted `market_snapshots` reads.

    `not_applicable`: the caller's own call, not decided here -- this
    function only knows about price readings, not which symbols are
    indices. Confirmed live, 2026-09-17: pure indices (SPX/NDX/VIX)
    always have volume=0 (see AnchoredVwap's own docstring), so callers
    that know the underlying's kind should pass `not_applicable=True`
    for those rather than let this silently return `provisional=True`
    forever, which reads as "still accumulating" for something that
    will never arrive.
    """
    anchor = calculate_session_open(as_of)
    anchor_utc = anchor.astimezone(UTC)
    if not_applicable:
        return AnchoredVwap(
            value=None,
            provisional=False,
            anchor_time=anchor_utc,
            sample_count=0,
            not_applicable=True,
        )
    session_reading_count = len(
        [reading for reading in readings if anchor <= reading.as_of <= as_of]
    )
    series = calculate_anchored_vwap_series(readings, as_of)

    if not series:
        return AnchoredVwap(
            value=None,
            provisional=True,
            anchor_time=anchor_utc,
            sample_count=session_reading_count,
        )

    return AnchoredVwap(
        value=series[-1][1],
        provisional=False,
        anchor_time=anchor_utc,
        sample_count=session_reading_count,
    )
