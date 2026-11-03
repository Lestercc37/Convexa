"""5-day replay, phase 1 (docs/diseno-replay-5-dias-2026-10-29.txt): one stored session at a time, read in one request.

Nothing new is stored or calculated: the candles are the session's stored prices (market_snapshots) bucketed to 1 and 5 minutes, the
levels are every stored scheduler cycle of that day (gamma_aggregates: Call Wall, Put Wall, ABS, Flip), and the anchored VWAP is the same
formula the live chart uses, run over that day's readings. The heatmap is NOT here: the page asks the existing /gamma/{symbol}/heatmap for
the day's window (2-minute buckets), a fifth of the 5-day cost measured in the design.

ES / NQ are their index (SPX / NDX) shifted by THAT day's typed anchor (future_price_anchors), never today's; a day without an anchor is
shown as the index itself and says so (`anchored` False, offset 0).
"""

from __future__ import annotations

import time as monotonic_clock
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from backend.domain.entities import LevelCycle, MarketPrice, PriceCandle
from backend.domain.ports import IAsyncMarketReadStorage
from backend.domain.use_cases.calculate_anchored_vwap import (
    calculate_anchored_vwap_series,
    calculate_proxy_anchored_vwap_series,
    opening_reference,
)
from backend.domain.use_cases.futures_proxy import proxy_symbol_for
from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.domain.use_cases.read_models import VWAP_PROXY_SYMBOL_BY_INDEX, _is_pure_index

# Sessions offered besides today ("Hoy (en vivo)" plus the last 5 sessions with data), found in the last REPLAY_SCAN_DAYS calendar days
# (covers holidays and long weekends).
REPLAY_SESSIONS = 5
REPLAY_SCAN_DAYS = 14
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
# A finished day never changes: it is held for 10 minutes. Today's is re-read at most every 5 s (the page polls it every 15 s while live).
_PAST_DAY_TTL_SECONDS = 600.0
_TODAY_TTL_SECONDS = 5.0
_DAYS_TTL_SECONDS = 60.0
_day_cache: dict[tuple[str, date, str], tuple[float, "ReplayDay"]] = {}
_days_cache: dict[tuple[str, date], tuple[float, "ReplayDays"]] = {}


@dataclass(frozen=True, slots=True)
class ReplayDays:
    symbol: str
    today: date
    today_has_data: bool
    sessions: tuple[date, ...]  # the earlier sessions with data, newest first, at most REPLAY_SESSIONS


@dataclass(frozen=True, slots=True)
class ReplayDay:
    symbol: str
    source_symbol: str  # whose stored data is shown (SPX for ES, NDX for NQ, the symbol itself otherwise)
    session_date: date
    view: str
    live: bool  # today's session before its close: the page keeps polling
    session_open: datetime
    session_close: datetime
    price_offset: Decimal  # added to every source price and level (ES / NQ: that day's anchor minus the index's opening reference)
    anchored: bool | None  # ES / NQ: whether that day had an anchor; None for every other symbol
    candles_1m: tuple[PriceCandle, ...]
    candles_5m: tuple[PriceCandle, ...]
    levels: tuple[LevelCycle, ...]
    vwap: tuple[tuple[datetime, Decimal], ...]  # one point per minute (the minute's last value), stamped at the start of the minute
    vwap_not_applicable: bool
    vwap_proxy_symbol: str | None


def session_window(day: date) -> tuple[datetime, datetime]:
    return datetime.combine(day, SESSION_OPEN, EASTERN_TIME), datetime.combine(day, SESSION_CLOSE, EASTERN_TIME)


def bucket_candles(readings: list[MarketPrice], session_open: datetime, bucket_seconds: int, offset: Decimal = Decimal(0)) -> tuple[PriceCandle, ...]:
    """OHLC per bucket counted from 09:30 ET (the same buckets as /market/{symbol}/candles); open/close = first/last stored price of the bucket."""
    candles: dict[int, list[Decimal]] = {}
    for reading in sorted(readings, key=lambda item: item.as_of):
        index = int((reading.as_of - session_open).total_seconds() // bucket_seconds)
        if index < 0:
            continue
        price = reading.price + offset
        held = candles.get(index)
        if held is None:
            candles[index] = [price, price, price, price]
        else:
            held[1] = max(held[1], price)
            held[2] = min(held[2], price)
            held[3] = price
    return tuple(
        PriceCandle(time=session_open + timedelta(seconds=index * bucket_seconds), open=o, high=h, low=lo, close=c)
        for index, (o, h, lo, c) in sorted(candles.items())
    )


def per_minute(series: list[tuple[datetime, Decimal]], offset: Decimal = Decimal(0)) -> tuple[tuple[datetime, Decimal], ...]:
    """The last value of each minute, stamped at the start of that minute (where the 1-minute candle sits)."""
    minutes: dict[datetime, Decimal] = {}
    for stamp, value in sorted(series, key=lambda point: point[0]):
        minutes[stamp.replace(second=0, microsecond=0)] = value + offset
    return tuple(sorted(minutes.items()))


def shift_level(cycle: LevelCycle, offset: Decimal) -> LevelCycle:
    if offset == 0:
        return cycle

    def moved(value: Decimal | None) -> Decimal | None:
        return None if value is None else value + offset

    return LevelCycle(cycle.time, moved(cycle.call_wall), moved(cycle.put_wall), moved(cycle.absolute_gamma_strike), moved(cycle.gamma_flip))


async def get_replay_days(storage: IAsyncMarketReadStorage, symbol: str, now: datetime | None = None) -> ReplayDays:
    now = now or datetime.now(EASTERN_TIME)
    today = now.astimezone(EASTERN_TIME).date()
    source = (proxy_symbol_for(symbol) or symbol).upper()
    key = (source, today)
    held = _days_cache.get(key)
    clock = monotonic_clock.monotonic()
    if held is not None and clock - held[0] < _DAYS_TTL_SECONDS:
        days = held[1]
    else:
        found = await storage.get_session_dates(source, today - timedelta(days=REPLAY_SCAN_DAYS), today)
        days = ReplayDays(
            symbol=source,
            today=today,
            today_has_data=today in found,
            sessions=tuple(day for day in found if day < today)[:REPLAY_SESSIONS],
        )
        if len(_days_cache) > 64:
            _days_cache.clear()
        _days_cache[key] = (clock, days)
    return ReplayDays(symbol=symbol.upper(), today=days.today, today_has_data=days.today_has_data, sessions=days.sessions)


async def get_replay_day(
    storage: IAsyncMarketReadStorage, symbol: str, day: date, view: str = "tactical", now: datetime | None = None
) -> ReplayDay:
    now = now or datetime.now(EASTERN_TIME)
    symbol = symbol.upper()
    session_open, session_close = session_window(day)
    live = day == now.astimezone(EASTERN_TIME).date() and now < session_close
    key = (symbol, day, view)
    clock = monotonic_clock.monotonic()
    held = _day_cache.get(key)
    if held is not None and clock - held[0] < (_TODAY_TTL_SECONDS if live or day >= now.astimezone(EASTERN_TIME).date() else _PAST_DAY_TTL_SECONDS):
        return held[1]
    replay = await _read_day(storage, symbol, day, view, session_open, session_close, live)
    if len(_day_cache) > 128:
        _day_cache.clear()
    _day_cache[key] = (clock, replay)
    return replay


async def _read_day(
    storage: IAsyncMarketReadStorage,
    symbol: str,
    day: date,
    view: str,
    session_open: datetime,
    session_close: datetime,
    live: bool,
) -> ReplayDay:
    future_proxy = proxy_symbol_for(symbol)
    source = (future_proxy or symbol).upper()
    # Readings strictly before 16:00: the 16:00 print belongs to no regular-session candle.
    end = session_close - timedelta(microseconds=1)
    readings = await storage.get_price_history(source, session_open, end)
    offset = Decimal(0)
    anchored: bool | None = None
    if future_proxy is not None:
        anchor = await storage.get_future_price_anchor(symbol, day)
        reference = opening_reference(readings) if readings else None
        anchored = anchor is not None and reference is not None
        if anchored:
            offset = anchor - reference[0]

    vwap_proxy = VWAP_PROXY_SYMBOL_BY_INDEX.get(source)
    if vwap_proxy is not None:
        proxy_readings = await storage.get_price_history(vwap_proxy, session_open, end)
        vwap_series = calculate_proxy_anchored_vwap_series(readings, proxy_readings, end)
        vwap_not_applicable = False
    elif _is_pure_index(source):
        vwap_series, vwap_not_applicable = [], True
    else:
        vwap_series = calculate_anchored_vwap_series(readings, end)
        vwap_not_applicable = False

    levels = await storage.get_level_history(source, session_open, session_close, view)
    return ReplayDay(
        symbol=symbol,
        source_symbol=source,
        session_date=day,
        view=view,
        live=live,
        session_open=session_open,
        session_close=session_close,
        price_offset=offset,
        anchored=anchored,
        candles_1m=bucket_candles(readings, session_open, 60, offset),
        candles_5m=bucket_candles(readings, session_open, 300, offset),
        levels=tuple(shift_level(cycle, offset) for cycle in levels),
        vwap=per_minute(vwap_series, offset),
        vwap_not_applicable=vwap_not_applicable,
        vwap_proxy_symbol=vwap_proxy,
    )


def clear_replay_cache() -> None:
    """Test hook."""
    _day_cache.clear()
    _days_cache.clear()
