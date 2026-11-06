"""Options flow PRESSURE per strike and minute, for the blue 'heatmap de presión' in NinjaTrader (docs/diseno-heatmap-presion-2026-10-29.txt, section 4;
Lester 2026-11-06: build it inside NinjaTrader).

Pressure of a strike = its signed delta flow (table minute_strike_flow: buying calls / selling puts +, buying puts / selling calls -; multi-leg
packages are not in delta_flow) accumulated minute by minute with a decay: every minute the previous pressure keeps 0.5^(1/HALF_LIFE_MINUTES),
so a level fed 15 minutes ago weighs half and an old one fades away on its own. Calls and puts of a strike are added up (both signed bullish +).
Pure logic, no I/O."""

from __future__ import annotations

from datetime import datetime, timedelta

HALF_LIFE_MINUTES = 15.0


def pressure_matrix(
    rows: list[tuple[datetime, float, float]], start: datetime, end: datetime, half_life_minutes: float = HALF_LIFE_MINUTES
) -> tuple[list[float], list[tuple[datetime, list[float]]]]:
    """(strikes ascending, [(minute, pressure per strike)]) for every minute from `start` up to the last minute before `end`, minutes without
    flow included (pressure keeps decaying). `rows` = (minute start, strike, net delta flow of that minute), any order, several per cell allowed."""
    strikes = sorted({strike for _, strike, _ in rows})
    if not strikes:
        return [], []
    index = {strike: i for i, strike in enumerate(strikes)}
    by_minute: dict[datetime, list[float]] = {}
    for minute, strike, flow in rows:
        cell = by_minute.setdefault(minute, [0.0] * len(strikes))
        cell[index[strike]] += flow
    keep = 0.5 ** (1.0 / half_life_minutes)
    pressure = [0.0] * len(strikes)
    out: list[tuple[datetime, list[float]]] = []
    minute = start.replace(second=0, microsecond=0)
    while minute < end:
        flows = by_minute.get(minute)
        pressure = [p * keep + (flows[i] if flows else 0.0) for i, p in enumerate(pressure)]
        out.append((minute, list(pressure)))
        minute += timedelta(minutes=1)
    return strikes, out
