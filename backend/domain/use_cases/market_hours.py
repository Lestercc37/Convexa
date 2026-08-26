from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

EASTERN_TIME = ZoneInfo("America/New_York")
MARKET_OPEN_ET = time(9, 30)
MARKET_CLOSE_ET = time(16, 0)
_WEEKEND_WEEKDAYS = (5, 6)  # datetime.weekday(): Saturday=5, Sunday=6


def is_market_open(now: datetime) -> bool:
    """Whether US equity/options markets are open at `now`.

    Checks weekday (Mon-Fri) and 9:30am-4:00pm ET, half-open interval
    `[9:30, 16:00)` — no exchange holiday calendar. Known, deliberate
    limitation: this will still report the market as open on a weekday
    that happens to be a market holiday (Thanksgiving, Christmas, etc.),
    since no such calendar exists anywhere in this project yet. Building
    one is out of scope for the scheduler that consumes this function
    (`backend/core/scheduler.py`) — see docs/dashboard-spec.md.
    """
    eastern_now = now.astimezone(EASTERN_TIME)
    if eastern_now.weekday() in _WEEKEND_WEEKDAYS:
        return False
    return MARKET_OPEN_ET <= eastern_now.time() < MARKET_CLOSE_ET
