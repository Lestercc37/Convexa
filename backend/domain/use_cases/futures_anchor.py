"""The ES / NQ opening anchor saved automatically (Lester 2026-11-02 "si" to the auto-save, 2026-11-04 "si" to NinjaTrader as the source).

ANCHOR = (high + low + close) / 3 of the 09:30 ET 1-minute bar, rounded to 0.25: the number Lester types as the opening price. It reaches
Convexa from his NinjaTrader at 09:31 (indicator 'Convexa Ancla', POST /api/v1/feeds/futures-anchor) or from the 09:41 Yahoo task
(backend/scripts/es_nq_anchor_telegram.py). Every automatic save: only when the day has no anchor yet (a typed number is never overwritten),
and only when the offset to the index's first minute (anchor - median of SPX / NDX readings 09:30:00-09:30:59) is within a margin of the
last days' offsets (the basis shrinks as the contract nears expiry, so a fixed range goes stale)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from backend.domain.ports import IStorage


@dataclass(frozen=True)
class FutureSpec:
    index_symbol: str
    usual_offset: tuple[int, int]  # used only while no earlier day has an anchor
    margin: float  # allowed distance from the last days' median offset


FUTURE_SPECS: dict[str, FutureSpec] = {
    "ES": FutureSpec("SPX", (40, 60), 5),
    "NQ": FutureSpec("NDX", (190, 250), 15),
}
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


def check_anchor(future: str, anchor: Decimal, first_minute_median: float | None, history_offsets: list[float]) -> AnchorCheck:
    spec = FUTURE_SPECS[future]
    if first_minute_median is None:
        return AnchorCheck(False, None, None, f"sin lecturas de {spec.index_symbol} en el minuto 09:30: no se puede comprobar")
    offset = float(anchor) - first_minute_median
    if history_offsets:
        usual = statistics.median(history_offsets)
        if abs(offset - usual) > spec.margin:
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
    history_offsets: list[float],
    dry_run: bool = False,
) -> AutoSaveResult:
    check = check_anchor(future, anchor, first_minute_median, history_offsets)
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
