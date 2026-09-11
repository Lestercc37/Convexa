from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from backend.domain.entities import MarketHoliday, MarketHolidayType

EASTERN_TIME = ZoneInfo("America/New_York")
MARKET_OPEN_ET = time(9, 30)
MARKET_CLOSE_ET = time(16, 0)
_WEEKEND_WEEKDAYS = (5, 6)  # datetime.weekday(): Saturday=5, Sunday=6


def is_market_open(now: datetime, holidays: Mapping[date, MarketHoliday] | None = None) -> bool:
    """Whether US equity/options markets are open at `now`.

    Checks weekday (Mon-Fri) and 9:30am-4:00pm ET, half-open interval
    `[9:30, 16:00)`, adjusted for `holidays` when given -- a full closure
    (Thanksgiving, Christmas, etc.) makes the whole day closed regardless
    of time; an early closure (e.g. 1:00pm ET the Friday after
    Thanksgiving) narrows the interval to that day's own `open`/`close`
    instead of the fixed ones. `holidays` is data the caller already
    resolved (see `IDataProvider.get_market_holidays`), keyed by calendar
    date in US/Eastern -- this function stays pure and never fetches
    anything itself, same as before this parameter existed.

    Known, deliberate limitation when `holidays` is omitted (`None`, the
    default): this reports the market as open on a weekday that happens to
    be a market holiday. `UnderlyingRefreshScheduler`
    (`backend/core/scheduler.py`) always resolves and passes real holiday
    data now (see its own docstring) -- this default only still applies to
    this function's other, lower-stakes callers
    (`stream_underlying_price.py`, `read_models.py`,
    `adapters/providers/thetadata/provider.py`'s own internal check),
    which don't yet. See docs/dashboard-spec.md.
    """
    eastern_now = now.astimezone(EASTERN_TIME)
    if eastern_now.weekday() in _WEEKEND_WEEKDAYS:
        return False
    holiday = holidays.get(eastern_now.date()) if holidays else None
    if holiday is not None:
        if holiday.closure_type is MarketHolidayType.FULL_CLOSE:
            return False
        open_time = holiday.open if holiday.open is not None else MARKET_OPEN_ET
        close_time = holiday.close if holiday.close is not None else MARKET_CLOSE_ET
        return open_time <= eastern_now.time() < close_time
    return MARKET_OPEN_ET <= eastern_now.time() < MARKET_CLOSE_ET
