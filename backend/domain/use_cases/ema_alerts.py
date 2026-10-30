"""EMA cross alerts (migration 0051): the same EMA the price chart draws (frontend/lib/ema.ts emaValues: seeded with the first close, alpha = 2 / (period + 1), over the
previous sessions' candles plus today's), evaluated on CLOSED candles only, so an alert never fires on a candle that can still change. ES / NQ use SPX / NDX candles: the
futures are the index shifted by a constant, so their crossings are the same minute.

A crossing = the sign of (fast - slow) on the last closed candle differs from the sign stored for the rule. The first evaluation of a rule only stores the sign (no alert for a
cross that happened before the rule existed). Pure functions: the worker (backend/ema_alerts_worker.py) does the I/O.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.domain.use_cases.market_hours import EASTERN_TIME

TIMEFRAME_SECONDS: dict[str, int] = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
# Warm-up, same as the chart: 5 sessions of 1-minute candles, 10 of the others.
SEED_SESSIONS: dict[str, int] = {"1m": 5, "5m": 10, "15m": 10, "1h": 10}
CANDLE_SOURCE_SYMBOL: dict[str, str] = {"ES": "SPX", "NQ": "NDX"}
LINK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
LINK_CODE_LENGTH = 6
LINK_CODE_TTL = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class EmaRule:
    id: int
    user_id: int
    symbol: str
    timeframe: str
    fast_period: int
    slow_period: int
    direction: str  # up | down | both
    last_state: int | None
    last_candle: datetime | None


@dataclass(frozen=True, slots=True)
class CrossResult:
    state: int  # +1 fast above slow, -1 below (0 = equal: keeps the previous side)
    candle: datetime  # start of the last closed candle
    fired: str | None  # "up" | "down" when this rule must alert
    fast: float
    slow: float
    close: float


def ema_series(closes: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    values: list[float] = []
    ema = 0.0
    for index, close in enumerate(closes):
        ema = close if index == 0 else alpha * close + (1 - alpha) * ema
        values.append(ema)
    return values


def closed_candles(
    candles: list[tuple[datetime, float]], timeframe: str, now: datetime
) -> list[tuple[datetime, float]]:
    """(start, close) of the candles whose bucket has ended at `now`; the session's last bucket ends at 16:00 ET even when shorter (the 15:30 hourly candle)."""
    length = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])

    def end_of(start: datetime) -> datetime:
        close = start.astimezone(EASTERN_TIME).replace(hour=16, minute=0, second=0, microsecond=0)
        return min(start + length, close)

    return [candle for candle in candles if end_of(candle[0]) <= now]


def evaluate(rule: EmaRule, candles: list[tuple[datetime, float]]) -> CrossResult | None:
    """`candles` = closed candles, oldest first (the warm-up sessions included). None when there is not enough history for the slow EMA or nothing new closed."""
    if len(candles) < rule.slow_period:
        return None
    last_start, last_close = candles[-1]
    if rule.last_candle is not None and last_start <= rule.last_candle:
        return None
    closes = [close for _, close in candles]
    fast, slow = ema_series(closes, rule.fast_period)[-1], ema_series(closes, rule.slow_period)[-1]
    diff = fast - slow
    state = 1 if diff > 0 else -1 if diff < 0 else (rule.last_state or 0)
    fired = None
    if rule.last_state in (1, -1) and state in (1, -1) and state != rule.last_state:
        direction = "up" if state == 1 else "down"
        if rule.direction in (direction, "both"):
            fired = direction
    return CrossResult(
        state=state, candle=last_start, fired=fired, fast=fast, slow=slow, close=last_close
    )


def alert_text(rule: EmaRule, result: CrossResult) -> str:
    arrow = "▲ cruza AL ALZA" if result.fired == "up" else "▼ cruza A LA BAJA"
    when = result.candle.strftime("%H:%M")
    return (
        f"Convexa · {rule.symbol} {rule.timeframe}\n"
        f"EMA {rule.fast_period} {arrow} la EMA {rule.slow_period}\n"
        f"Vela de las {when} ET cerrada · cierre {result.close:,.2f} · EMA {rule.fast_period} {result.fast:,.2f} / EMA {rule.slow_period} {result.slow:,.2f}"
    )


def new_link_code() -> str:
    return "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))


def code_from_message(text: str) -> str | None:
    """'/start ABC123', 'abc123' or 'Codigo: abc-123' -> 'ABC123'; None when no 6-character code is in the message."""
    cleaned = "".join(ch for ch in text.upper() if ch.isalnum())
    if cleaned.startswith("START"):
        cleaned = cleaned[5:]
    tail = cleaned[-LINK_CODE_LENGTH:]
    return (
        tail
        if len(tail) == LINK_CODE_LENGTH and all(ch in LINK_CODE_ALPHABET for ch in tail)
        else None
    )
