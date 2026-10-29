"""The flow chain view (docs/diseno-cadena-flujo-2026-10-25.txt): per strike, the signed option flow of the last 5 / 15 / 30 / 60 minutes and of the day, calls and puts apart and netted,
multi-leg packages apart, read from minute_strike_flow (migration 0050). Only finished minutes count: the windows end at the start of the minute of `at`.

Expirations: "0dte" = the session's own expiration, "0-2dte" = the first three expirations at or after the session day that traded today, "all" = every expiration the stream follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from backend.domain.entities import StrikeFlowSummary
from backend.domain.use_cases.market_hours import EASTERN_TIME

WINDOWS_MINUTES = (5, 15, 30, 60)
EXPIRATION_MODES = ("0dte", "0-2dte", "all")


@dataclass(frozen=True, slots=True)
class StrikeFlowRow:
    strike: float
    net: dict[int, float]  # window minutes -> calls + puts
    calls: dict[int, float]
    puts: dict[int, float]
    net_day: float
    packages_15: int
    contracts_signed_day: int
    contracts_package_day: int
    contracts_other_day: int


@dataclass(frozen=True, slots=True)
class StrikeFlowChain:
    symbol: str
    as_of: datetime
    session_start: datetime
    expirations: list[date]
    rows: list[StrikeFlowRow]

    @property
    def net_day(self) -> float:
        return sum(row.net_day for row in self.rows)

    @property
    def signed_share_day(self) -> float | None:
        total = sum(
            row.contracts_signed_day + row.contracts_package_day + row.contracts_other_day
            for row in self.rows
        )
        return sum(row.contracts_signed_day for row in self.rows) / total if total else None


def session_bounds(at: datetime) -> tuple[datetime, datetime]:
    """(09:30 ET of `at`'s day, the start of `at`'s minute capped at 16:00 ET)."""
    local = at.astimezone(EASTERN_TIME)
    open_ = local.replace(hour=9, minute=30, second=0, microsecond=0)
    close = local.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_, min(local.replace(second=0, microsecond=0), close)


def pick_expirations(mode: str, traded: list[date], session_day: date) -> list[date] | None:
    if mode == "all":
        return None
    upcoming = sorted(day for day in traded if day >= session_day)
    if mode == "0dte":
        return [session_day] if session_day in upcoming else upcoming[:1]
    return upcoming[:3]


def build_chain(
    symbol: str,
    as_of: datetime,
    session_start: datetime,
    expirations: list[date],
    summaries: list[StrikeFlowSummary],
) -> StrikeFlowChain:
    by_strike: dict[float, list[StrikeFlowSummary]] = {}
    for summary in summaries:
        by_strike.setdefault(summary.strike, []).append(summary)
    rows = []
    for strike in sorted(by_strike):
        parts = by_strike[strike]
        calls = {
            w: sum(p.flows[w] for p in parts if p.option_right == "C") for w in WINDOWS_MINUTES
        }
        puts = {w: sum(p.flows[w] for p in parts if p.option_right == "P") for w in WINDOWS_MINUTES}
        rows.append(
            StrikeFlowRow(
                strike=strike,
                net={w: calls[w] + puts[w] for w in WINDOWS_MINUTES},
                calls=calls,
                puts=puts,
                net_day=sum(p.flow_day for p in parts),
                packages_15=sum(p.packages_15 for p in parts),
                contracts_signed_day=sum(p.contracts_signed_day for p in parts),
                contracts_package_day=sum(p.contracts_package_day for p in parts),
                contracts_other_day=sum(p.contracts_other_day for p in parts),
            )
        )
    return StrikeFlowChain(
        symbol=symbol, as_of=as_of, session_start=session_start, expirations=expirations, rows=rows
    )


def window_starts(end: datetime) -> dict[int, datetime]:
    return {w: end - timedelta(minutes=w) for w in WINDOWS_MINUTES}
