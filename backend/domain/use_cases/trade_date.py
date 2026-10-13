"""OPRA / Cboe trade date for option volume.

ThetaData (support, 2026-10-13) and thinkorswim count a contract's daily volume by TRADE DATE, not by
calendar day: a trade date opens with the evening Global Trading Hours session at 20:15 ET of the PREVIOUS
calendar day (SPX complex: GTH 20:15 -> 09:25 ET next morning), then the regular session, and the
snapshot/history ohlc volume covers all of it. Resetting the stream counters at 00:00 ET dropped the
20:15-23:59 volume of every contract (the near-constant per-contract shortfall against thinkorswim,
confirmed 100.00% on 20 SPXW contracts on 2026-10-09).

So the counters now roll at 20:15 ET. Prints between 16:16 and 20:14 (stray after-close prints) still
belong to the day that just ended, exactly as on ThetaData's bars.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from backend.domain.use_cases.market_hours import EASTERN_TIME

TRADE_DATE_OPENS_ET = time(20, 15)


def trade_date_of(now: datetime) -> date:
    """The trade date `now` belongs to: from 20:15 ET on, the NEXT calendar day. (Friday evening maps to
    Saturday, which never trades; Sunday 20:15 maps to Monday, which is the real Sunday-night session.)"""
    eastern = now.astimezone(EASTERN_TIME)
    if eastern.time() >= TRADE_DATE_OPENS_ET:
        return eastern.date() + timedelta(days=1)
    return eastern.date()


def trade_date_start(now: datetime) -> datetime:
    """When the trade date `now` belongs to opened: 20:15 ET of the calendar day before trade_date_of(now)."""
    return datetime.combine(trade_date_of(now) - timedelta(days=1), TRADE_DATE_OPENS_ET, tzinfo=EASTERN_TIME)


def next_trade_date_roll(now: datetime) -> datetime:
    """The next 20:15 ET strictly after `now` (the moment the counters must roll)."""
    eastern = now.astimezone(EASTERN_TIME)
    day = eastern.date() if eastern.time() < TRADE_DATE_OPENS_ET else eastern.date() + timedelta(days=1)
    return datetime.combine(day, TRADE_DATE_OPENS_ET, tzinfo=EASTERN_TIME)


# One-time transition. Until the processor restarts with the trade-date rule (deployed 2026-10-13 BEFORE 20:15 ET),
# contract_cumulative_volume rows written between 20:15 and 24:00 are the OLD per-calendar-day counters, i.e. the
# whole day's total of the day that just ended (2026-10-12 23:59:48: 727 rows, 13.56M contracts): reading them as
# part of the NEW trade date would credit a day's volume to the next one. So until the first 20:15 ET under the new
# rule, rows are only trusted from the calendar day's start (the old behaviour); from then on, from the trade date's
# opening. Must be kept after the deployment day (it is a no-op later) or bumped if the deployment slips past 20:15.
TRADE_DATE_COUNTERS_FROM = datetime(2026, 10, 13, 20, 15, tzinfo=EASTERN_TIME)


def volume_rows_since(now: datetime) -> datetime:
    """The oldest `contract_cumulative_volume.updated_at` that still counts as the current trade date's volume
    (used by the stream processor's resume and by the readers of the table)."""
    opened = trade_date_start(now)
    if now < TRADE_DATE_COUNTERS_FROM:
        eastern = now.astimezone(EASTERN_TIME)
        return max(opened, eastern.replace(hour=0, minute=0, second=0, microsecond=0))
    return opened
