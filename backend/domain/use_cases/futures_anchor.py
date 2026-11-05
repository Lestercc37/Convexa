"""The ES / NQ opening anchor saved automatically (Lester 2026-11-02 "si" to the auto-save, 2026-11-04 "si" to NinjaTrader as the source).

ANCHOR = (high + low + close) / 3 of the 09:30 ET 1-minute bar, rounded to 0.25: the number Lester types as the opening price. It reaches
Convexa from his NinjaTrader at 09:31 (indicator 'Convexa Ancla', POST /api/v1/feeds/futures-anchor) or from the 09:41 Yahoo task
(backend/scripts/es_nq_anchor_telegram.py). Every automatic save: only when the day has no anchor yet (a typed number is never overwritten),
and only when the offset to the index's first minute (anchor - median of SPX / NDX readings 09:30:00-09:30:59) is within a margin of the
offset expected from the last days (the basis shrinks as the contract nears expiry, so a fixed range goes stale).

The expected offset (study docs/estudio_margen_ancla_es_nq_2026-11-05.txt, after the check rejected 2026-11-05's good NinjaTrader values):
the basis is about proportional to the days left to the contract's expiry, so the last 5 days' offset / days-left, times today's days-left
(the plain median of the last 5 offsets lagged the shrink: ES -7.2 and NQ -40.1 on 11-05). The margin grows with the index's own first-minute
range, the noise of the median it is compared with: ES 5 + 0.5 x range, NQ 20 + 0.5 x range accept every typed anchor since 10-12
(17 ES / 16 NQ days, the closest at 6.5 / 8 points inside), while a wrong bar or a typo is still far outside."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from backend.domain.ports import IStorage


@dataclass(frozen=True)
class FutureSpec:
    index_symbol: str
    usual_offset: tuple[int, int]  # used only while no earlier day has an anchor
    margin: float  # allowed distance from the expected offset, plus RANGE_SHARE x the index's first-minute range


FUTURE_SPECS: dict[str, FutureSpec] = {
    "ES": FutureSpec("SPX", (40, 60), 5),
    "NQ": FutureSpec("NDX", (190, 250), 20),
}
RANGE_SHARE = 0.5
BASIS_DAYS = 5
# ES / NQ roll to the next quarterly contract 8 days before expiry (CME roll date, the Thursday before the third Friday's week);
# within ROLL_WINDOW_DAYS of it either contract is accepted, since NinjaTrader and Yahoo may switch a day apart.
ROLL_DAYS_BEFORE_EXPIRY = 8
ROLL_WINDOW_DAYS = 3


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def contract_expiry(day: date) -> date:
    """The expiry of the quarterly ES / NQ contract that is front on `day` (Mar / Jun / Sep / Dec, the next one from its roll date on)."""
    year, month = day.year, ((day.month - 1) // 3 + 1) * 3
    while True:
        expiry = _third_friday(year, month)
        if day < expiry - timedelta(days=ROLL_DAYS_BEFORE_EXPIRY):
            return expiry
        year, month = (year + 1, 3) if month == 12 else (year, month + 3)


def _days_left(day: date, expiry: date) -> int:
    return max(1, (expiry - day).days)


def expected_offsets(history: list[tuple[date, float]], session_date: date) -> list[float]:
    """The offset today should have: the median basis per day left of the last BASIS_DAYS anchors x today's days left (one value, two around the
    roll date: the outgoing and the incoming contract). Empty without history."""
    rates = [offset / _days_left(day, contract_expiry(day)) for day, offset in history[:BASIS_DAYS]]
    if not rates:
        return []
    rate = statistics.median(rates)
    front = contract_expiry(session_date)
    expiries = [front]
    roll = front - timedelta(days=ROLL_DAYS_BEFORE_EXPIRY)
    previous_roll = contract_expiry(session_date - timedelta(days=ROLL_WINDOW_DAYS))
    if (roll - session_date).days <= ROLL_WINDOW_DAYS:
        expiries.append(contract_expiry(roll))  # the next contract, a few days early
    elif previous_roll != front:
        expiries.append(previous_roll)  # the outgoing contract, a few days late
    return [rate * _days_left(session_date, expiry) for expiry in expiries]
# NinjaTrader charts may be the micro contracts: same index points, same anchor.
MICRO_TO_FULL = {"MES": "ES", "MNQ": "NQ"}
SOURCES = ("manual", "ninjatrader", "yahoo")


def normalize_future(name: str) -> str | None:
    """'ES' / 'MES' / 'ES 12-26' / 'MNQ 12-26' -> 'ES' / 'NQ'; None for anything else."""
    root = name.strip().upper().split(" ")[0]
    root = MICRO_TO_FULL.get(root, root)
    return root if root in FUTURE_SPECS else None


def anchor_from_bar(high: Decimal, low: Decimal, close: Decimal) -> Decimal:
    typical = (Decimal(high) + Decimal(low) + Decimal(close)) / 3
    return (typical * 4).quantize(Decimal(1), rounding=ROUND_HALF_EVEN) / 4


@dataclass(frozen=True)
class AnchorCheck:
    ok: bool
    offset: float | None
    usual: float | None
    warning: str | None


def check_anchor(
    future: str,
    anchor: Decimal,
    first_minute_median: float | None,
    history: list[tuple[date, float]],
    session_date: date,
    first_minute_range: float = 0.0,
) -> AnchorCheck:
    """`history`: (day, offset) of the last days with an anchor, newest first (futures_anchor_queries.recent_offsets)."""
    spec = FUTURE_SPECS[future]
    if first_minute_median is None:
        return AnchorCheck(False, None, None, f"sin lecturas de {spec.index_symbol} en el minuto 09:30: no se puede comprobar")
    offset = float(anchor) - first_minute_median
    expected = expected_offsets(history, session_date)
    if expected:
        usual = min(expected, key=lambda value: abs(offset - value))
        if abs(offset - usual) > spec.margin + RANGE_SHARE * first_minute_range:
            return AnchorCheck(False, offset, usual, f"Ojo: {future} − {spec.index_symbol} = {offset:+.1f}; los últimos días fue {usual:+.1f}.")
        return AnchorCheck(True, offset, usual, None)
    low, high = spec.usual_offset
    if not (low - spec.margin <= offset <= high + spec.margin):
        return AnchorCheck(False, offset, None, f"Ojo: {future} − {spec.index_symbol} = {offset:+.1f}, fuera de lo habitual ({low:+d} a {high:+d}).")
    return AnchorCheck(True, offset, None, None)


@dataclass(frozen=True)
class AutoSaveResult:
    status: str  # "saved" | "would_save" (dry run) | "exists" | "check_failed"
    future: str
    anchor: Decimal
    check: AnchorCheck
    existing: Decimal | None = None
    existing_source: str | None = None


def auto_save_anchor(
    storage: IStorage,
    future: str,
    session_date: date,
    anchor: Decimal,
    source: str,
    first_minute_median: float | None,
    history: list[tuple[date, float]],
    dry_run: bool = False,
    first_minute_range: float = 0.0,
) -> AutoSaveResult:
    check = check_anchor(future, anchor, first_minute_median, history, session_date, first_minute_range)
    existing = storage.get_future_price_anchor(future, session_date)
    if existing is not None:
        return AutoSaveResult("exists", future, anchor, check, existing, storage.get_future_price_anchor_source(future, session_date))
    if not check.ok:
        return AutoSaveResult("check_failed", future, anchor, check)
    if dry_run:
        return AutoSaveResult("would_save", future, anchor, check)
    if not storage.add_future_price_anchor_if_absent(future, session_date, anchor, source):
        # Saved by someone else between the read above and this write (Lester typing it at 09:31, the other source).
        return AutoSaveResult(
            "exists", future, anchor, check, storage.get_future_price_anchor(future, session_date), storage.get_future_price_anchor_source(future, session_date)
        )
    return AutoSaveResult("saved", future, anchor, check)
