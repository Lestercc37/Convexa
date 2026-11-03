"""Call | Put chart of one strike (Lester 2026-10-30: "the call/put chart of the same contract, the vertical axis in dollars gained or lost"):
per minute, the mid of the 0DTE call and put of one strike from option_quote_minutes (the stream processor's last bid/ask of each minute,
migration 0040), and the dollars per contract since the first minute ((mid - first mid) x 100), with the range inside each minute (highest/
lowest mid and single-leg trade, migration 0052) so a spike that came back within the minute still shows. Read-only: nothing new is stored.

option_quote_minutes keeps a row only in the minutes a contract's quote changed, and only for the 0DTE contracts near the money the stream
follows; a minute without a row still holds the previous quote, so each series is the recorded rows as they are (no fill)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time

from backend.domain.entities import OptionQuoteMinute
from backend.domain.use_cases.futures_proxy import proxy_symbol_for

EASTERN = "America/New_York"
CONTRACT_MULTIPLIER = 100
# The 0DTE root the stream follows for each index (SPX's dailies are SPXW, NDX's are NDXP); ETFs and stocks use their own symbol.
ZERO_DTE_ROOT = {"SPX": "SPXW", "NDX": "NDXP", "VIX": "VIXW"}
_OCC = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def root_for(symbol: str) -> str:
    """ES -> SPXW, NQ -> NDXP (the futures read their index's options), SPX -> SPXW, SPY -> SPY."""
    base = (proxy_symbol_for(symbol) or symbol).upper()
    return ZERO_DTE_ROOT.get(base, base)


def occ_prefix(root: str, expiration: date) -> str:
    return f"{root}{expiration:%y%m%d}"


def occ_symbol(root: str, expiration: date, right: str, strike: float) -> str:
    return f"{occ_prefix(root, expiration)}{right}{round(strike * 1000):08d}"


def parse_occ(symbol: str) -> tuple[str, date, str, float] | None:
    match = _OCC.match(symbol)
    if match is None:
        return None
    root, ymd, right, strike = match.groups()
    return root, datetime.strptime(ymd, "%y%m%d").date(), right, int(strike) / 1000


@dataclass(frozen=True, slots=True)
class ContractPoint:
    minute: datetime
    bid: float
    ask: float
    mid: float
    pnl: float  # dollars per contract since the first minute of the series
    # The range inside the minute in the same dollars (migration 0052): highest/lowest of the mid and of the single-leg trades; equal to
    # pnl when only the last quote is known (rows recorded before 0052).
    pnl_high: float
    pnl_low: float


@dataclass(frozen=True, slots=True)
class ContractSeries:
    occ_symbol: str
    right: str
    points: tuple[ContractPoint, ...]


@dataclass(frozen=True, slots=True)
class ContractChart:
    symbol: str
    root: str
    session_date: date
    expiration: date
    strikes: tuple[float, ...]
    strike: float | None
    call: ContractSeries | None
    put: ContractSeries | None


def build_series(occ: str, right: str, rows: list[OptionQuoteMinute]) -> ContractSeries:
    points: list[ContractPoint] = []
    first_mid: float | None = None
    for row in sorted(rows, key=lambda r: r.minute):
        bid, ask = float(row.bid), float(row.ask)
        if ask <= 0 or bid > ask:
            continue  # no usable quote that minute (no offer, or crossed)
        mid = (max(bid, 0.0) + ask) / 2
        if first_mid is None:
            first_mid = mid
        extremes = [float(v) for v in (row.mid_high, row.mid_low, row.trade_high, row.trade_low) if v is not None]
        high, low = max([mid, *extremes]), min([mid, *extremes])
        pnl, pnl_high, pnl_low = (round((price - first_mid) * CONTRACT_MULTIPLIER, 2) for price in (mid, high, low))
        points.append(ContractPoint(row.minute, bid, ask, mid, pnl, pnl_high, pnl_low))
    return ContractSeries(occ, right, tuple(points))


def strikes_from_symbols(symbols: list[str], root: str, expiration: date) -> tuple[float, ...]:
    """Strikes that have BOTH a call and a put recorded that day."""
    rights: dict[float, set[str]] = {}
    for symbol in symbols:
        parsed = parse_occ(symbol)
        if parsed is None or parsed[0] != root or parsed[1] != expiration:
            continue
        rights.setdefault(parsed[3], set()).add(parsed[2])
    return tuple(sorted(k for k, r in rights.items() if r == {"C", "P"}))


def default_strike(strikes: tuple[float, ...], last_mids: dict[float, tuple[float, float]]) -> float | None:
    """The at-the-money strike at the last recorded minute: the smallest |call mid - put mid| (put-call parity), else the middle strike."""
    if not strikes:
        return None
    priced = [k for k in strikes if k in last_mids]
    if priced:
        return min(priced, key=lambda k: (abs(last_mids[k][0] - last_mids[k][1]), k))
    return strikes[len(strikes) // 2]


def session_window(day: date) -> tuple[datetime, datetime]:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(EASTERN)
    return datetime.combine(day, time(9, 30), zone), datetime.combine(day, time(16, 0), zone)


async def get_contract_chart(storage, symbol: str, day: date, strike: float | None) -> ContractChart:
    """Reads the day's recorded 0DTE contracts of `symbol` (two indexed queries) and returns the call and put series of `strike`
    (default: the at-the-money strike at the last recorded minute)."""
    root = root_for(symbol)
    start, end = session_window(day)
    symbols = await storage.get_option_quote_symbols(occ_prefix(root, day), start, end)
    strikes = strikes_from_symbols(symbols, root, day)
    chosen = strike if strike is not None and strike in strikes else None
    if chosen is None:
        last = await storage.get_last_option_quotes([occ_symbol(root, day, r, k) for k in strikes for r in ("C", "P")], start, end)
        mids: dict[float, tuple[float, float]] = {}
        for k in strikes:
            c, p = last.get(occ_symbol(root, day, "C", k)), last.get(occ_symbol(root, day, "P", k))
            if c is not None and p is not None and c.ask > 0 and p.ask > 0:
                mids[k] = ((float(c.bid) + float(c.ask)) / 2, (float(p.bid) + float(p.ask)) / 2)
        chosen = default_strike(strikes, mids)
    if chosen is None:
        return ContractChart(symbol.upper(), root, day, day, strikes, None, None, None)
    call_occ, put_occ = occ_symbol(root, day, "C", chosen), occ_symbol(root, day, "P", chosen)
    rows = await storage.get_option_quote_minutes([call_occ, put_occ], start, end)
    return ContractChart(
        symbol.upper(),
        root,
        day,
        day,
        strikes,
        chosen,
        build_series(call_occ, "C", [r for r in rows if r.occ_symbol == call_occ]),
        build_series(put_occ, "P", [r for r in rows if r.occ_symbol == put_occ]),
    )
