"""Suggested minimum size of the alert cubes, per symbol (item 12b, Lester 2026-10-22).

One shared minimum (200 delta-equivalent contracts) fits no symbol: a delta contract costs ~$19,000 of premium on NDX and ~$313 on SPY, so on the stored alerts of 2026-10-19..22 the chart drew 0
cubes a session for NDX and 160-270 for SPY / QQQ. The suggestion makes every symbol draw about TARGET_CUBES_PER_SESSION cubes a session: per completed session, the delta-equivalent size
(contracts x |delta|) of the TARGET-th largest alert that is not "FAR" (the default filter hides those), the median of that over the last SESSIONS sessions, rounded to a 1 / 2 / 5 x 10^k number.
The dashboard uses it as the default and lets the viewer type their own minimum per symbol (kept in their browser). Pure functions; the route caches the result for an hour.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from backend.domain.ports import IAsyncMarketReadStorage
from backend.domain.use_cases.flow import Moneyness, WhaleAlert

EASTERN = ZoneInfo("America/New_York")
TARGET_CUBES_PER_SESSION = 40
SESSIONS = 5
ALERTS_PER_SYMBOL_LIMIT = 6000  # ~5 sessions of the busiest symbol (NDX ~730 a session)
CUBE_SYMBOLS = ("SPX", "NDX", "SPY", "QQQ", "IWM", "NVDA", "VIX")
_CACHE_TTL_SECONDS = 3600.0
_cache: tuple[float, dict] | None = None


def nice_number(value: float) -> int:
    """The 1 / 2 / 5 x 10^k number closest to `value` in log scale (at least 1)."""
    if not value or value <= 1:
        return 1
    exponent = math.floor(math.log10(value))
    candidates = [mantissa * 10**exponent for mantissa in (1, 2, 5)] + [10 ** (exponent + 1)]
    return int(min(candidates, key=lambda candidate: abs(math.log(candidate) - math.log(value))))


def gross_size(alert: WhaleAlert) -> float:
    """Delta-equivalent contracts, contracts x |delta| (the cube's gross size)."""
    return float(alert.contracts or 0) * abs(float(alert.delta or 0))


def net_size(alert: WhaleAlert) -> float:
    """The net cube size the dashboard draws by default (frontend lib/alert-cubes.ts netDeltaContracts): gross x |buy - sell| / (buy + sell);
    an alert with no quote to sign against keeps its gross size."""
    gross = gross_size(alert)
    buy, sell = float(alert.estimated_buy_volume), float(alert.estimated_sell_volume)
    if alert.quote_unavailable or buy + sell <= 0:
        return gross
    return gross * abs(buy - sell) / (buy + sell)


def suggested_minimum(
    alerts: Sequence[WhaleAlert],
    today: date,
    target: int = TARGET_CUBES_PER_SESSION,
    sessions: int = SESSIONS,
    size_of: Callable[[WhaleAlert], float] = gross_size,
) -> int | None:
    """The suggested minimum for one symbol, or None when no completed session has at least `target` sizable alerts (the dashboard then keeps its legacy default).
    `size_of` picks gross (default) or net cube sizes."""
    by_day: dict[date, list[float]] = defaultdict(list)
    for alert in alerts:
        if alert.moneyness is Moneyness.FAR or alert.contracts is None or alert.delta is None:
            continue
        size = size_of(alert)
        if size <= 0:
            continue
        local = alert.as_of.astimezone(EASTERN)
        if local.date() >= today or not (9, 30) <= (local.hour, local.minute) < (16, 0):
            continue
        by_day[local.date()].append(size)
    recent_days = sorted(by_day, reverse=True)[:sessions]
    values = []
    for day in recent_days:
        ranked = sorted(by_day[day], reverse=True)
        if len(ranked) >= target:
            values.append(ranked[target - 1])
    if not values:
        return None
    return nice_number(statistics.median(values))


async def get_cube_minimums_async(storage: IAsyncMarketReadStorage, now: datetime | None = None, symbols: Sequence[str] = CUBE_SYMBOLS) -> dict:
    """`/alert-cubes/minimums`: {"schema_version", "target_per_session", "sessions", "minimums": {symbol: int}, "net_minimums": {symbol: int}} (gross and net
    cube sizes); symbols without enough history are left out. Cached for an hour."""
    global _cache  # process-local cache, same pattern as the candle cache in read_models.py
    clock = time.monotonic()
    if _cache is not None and clock - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    today = (now or datetime.now(EASTERN)).astimezone(EASTERN).date()
    minimums: dict[str, int] = {}
    net_minimums: dict[str, int] = {}
    for symbol in symbols:
        alerts = await storage.get_recent_whale_alerts(symbol, ALERTS_PER_SYMBOL_LIMIT)
        value = suggested_minimum(alerts, today)
        if value is not None:
            minimums[symbol] = value
        net_value = suggested_minimum(alerts, today, size_of=net_size)
        if net_value is not None:
            net_minimums[symbol] = net_value
    result = {
        "schema_version": 1,
        "target_per_session": TARGET_CUBES_PER_SESSION,
        "sessions": SESSIONS,
        "minimums": minimums,
        "net_minimums": net_minimums,
    }
    _cache = (clock, result)
    return result


def reset_cache() -> None:
    """For tests."""
    global _cache
    _cache = None
