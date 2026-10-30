"""Signed option delta flow per ET minute, kept in memory by the stream processor and flushed once a minute (table minute_option_flow, migration 0048).

What it counts: a SIMPLE option trade (OPRA condition 18) whose price is at or beyond the prevailing bid/ask of its contract. A price at or above the ask is a buy, at or below the bid a sell;
anything else (inside the spread, no quote yet, no delta yet, another condition such as a multi-leg or an auction) is NOT given a side and is only counted as unclassified, so the share of the volume
that carries a side is always stored next to the flow. The size of a trade is contracts x |delta| (the delta of the contract in the last chain snapshot of the scheduler, refreshed once a minute by
the processor, never per trade): the same "delta-equivalent contracts" unit as the alert cubes. Convention: buying a call +, selling a call -, buying a put -, selling a put + (positive = bullish
pressure on both sides). It is an ESTIMATE of customer flow from the aggressor side: not inventory, not the market maker's position, not the hedge in futures.

Audit sample (off by default): with an audit window set, every option trade of the audited underlyings inside that ET window is also kept as ONE row (time, contract, size, price, the bid/ask used,
condition, outcome, |delta|, signed contribution) and written to a small ndjson file, so the panel's numbers can be checked trade by trade. Outside the window or with no window the cost is one
truthiness test per trade.

Per strike (off by default, enable_strikes(); table minute_strike_flow, migration 0050, the flow chain view): the same trades are ALSO added to one cell per (minute, expiration, strike, right),
with the contracts split three ways that add up to the cell's volume: signed (the trades above that got a side), package (multi-leg legs, OPRA 130-144, kept apart because one leg cannot tell
the side or the net bet of the package) and other (inside the spread, no quote / delta yet, auctions, crosses, floor...), plus the dollar premium bought and sold of the signed trades.

Cost on the processor's hot path: a QUOTE that survived the repeat filter is one dict assignment (only for the configured underlyings); an option TRADE is a handful of dict lookups, one Decimal division
and one addition. The rows are built only at the minute flush, in a separate task.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.domain.entities import FlowEvent, MinuteOptionFlow, MinuteStrikeFlow, QuoteEvent

EASTERN = ZoneInfo("America/New_York")
SIMPLE_TRADE_CONDITION = 18
# OPRA multi-leg conditions (the legs of spreads, straddles, ratio and stock-tied packages): counted apart in the per-strike cells.
MULTI_LEG_CONDITIONS: frozenset[int] = frozenset(range(130, 145))
# Default underlyings: the tradeable ones (indices, the four liquid ETFs, NVDA). VIX options are left out (their own expiries and quoting).
MINUTE_FLOW_SYMBOLS: frozenset[str] = frozenset({"SPX", "NDX", "SPY", "QQQ", "IWM", "NVDA"})
# A flush that keeps failing must not grow memory without bound: the oldest minutes go first.
MAX_PENDING_BUCKETS = 5_000
# The audit sample is bounded too: a window of a few minutes of SPY at the open is ~30k trades; past the cap new rows are dropped (counted).
MAX_AUDIT_ROWS = 100_000

# bucket = [calls_delta_flow, puts_delta_flow, trades_classified, trades_unclassified, contracts_classified, contracts_total]
_CALLS, _PUTS, _TRADES_OK, _TRADES_NO, _CONTRACTS_OK, _CONTRACTS_ALL = range(6)
# strike cell = [delta_flow, contracts_signed, contracts_package, contracts_other, buy_premium, sell_premium]
_FLOW, _SIGNED, _PACKAGE, _OTHER, _BUY, _SELL = range(6)
# Per-strike cells are many more than the per-root buckets (~100 a minute for SPX 0DTE alone): a flush that keeps failing drops the oldest past this cap.
MAX_PENDING_STRIKE_CELLS = 200_000


def _root_and_right(occ_symbol: str) -> tuple[str, str]:
    """('SPXW', 'C') from 'SPXW261019C07700000': the letters before the 6-digit expiration, and the right character before the 8-digit strike."""
    index = 0
    while index < len(occ_symbol) and not occ_symbol[index].isdigit():
        index += 1
    return occ_symbol[:index], occ_symbol[-9]


def _expiration_and_strike(occ_symbol: str) -> tuple[date, float] | None:
    """(date(2026, 10, 19), 7700.0) from 'SPXW261019C07700000'; None when the symbol does not have the OCC shape."""
    try:
        stamp = occ_symbol[-15:-9]
        return date(2000 + int(stamp[:2]), int(stamp[2:4]), int(stamp[4:6])), int(occ_symbol[-8:]) / 1000
    except (ValueError, IndexError):
        return None


class MinuteFlowRecorder:
    def __init__(self, symbols: frozenset[str] = MINUTE_FLOW_SYMBOLS) -> None:
        self._symbols = symbols
        self._quotes: dict[str, tuple[Decimal, Decimal]] = {}
        self._deltas: dict[str, float] = {}
        self._meta: dict[str, tuple[str, str]] = {}
        self._buckets: dict[tuple[int, str, str], list[float]] = {}
        self._audit_symbols: frozenset[str] = frozenset()
        self._audit_from = self._audit_to = 0  # ET minute of the day, [from, to)
        self._audit_rows: list[dict] = []
        self.audit_dropped = 0
        self._strikes_on = False
        self._strike_meta: dict[str, tuple[date, float] | None] = {}
        self._strike_cells: dict[tuple[int, str, date, float, str], list[float]] = {}

    def enable_strikes(self) -> None:
        """Also keep the flow per strike (minute_strike_flow). Off by default: the table needs migration 0050."""
        self._strikes_on = True

    def _strike_cell(self, minute: int, symbol: str, occ: str, right: str) -> list[float] | None:
        meta = self._strike_meta.get(occ, False)
        if meta is False:
            meta = self._strike_meta[occ] = _expiration_and_strike(occ)
        if meta is None:
            return None
        key = (minute, symbol, meta[0], meta[1], right)
        cell = self._strike_cells.get(key)
        if cell is None:
            cell = self._strike_cells[key] = [0.0, 0, 0, 0, 0.0, 0.0]
            if len(self._strike_cells) > MAX_PENDING_STRIKE_CELLS:
                for old in sorted(self._strike_cells)[: len(self._strike_cells) - MAX_PENDING_STRIKE_CELLS]:
                    del self._strike_cells[old]
        return cell

    def set_audit(self, symbols: frozenset[str], window: str) -> None:
        """window = 'HH:MM-HH:MM' (ET, end exclusive). A bad window leaves the audit off."""
        try:
            start, end = window.split("-")
            first = int(start[:2]) * 60 + int(start[3:5])
            last = int(end[:2]) * 60 + int(end[3:5])
        except (ValueError, IndexError):
            return
        if last <= first or not symbols:
            return
        self._audit_symbols = symbols
        self._audit_from, self._audit_to = first, last

    def take_audit(self) -> list[dict]:
        rows, self._audit_rows = self._audit_rows, []
        return rows

    def _audit(self, stamp: datetime, event: FlowEvent, quote, delta, outcome: str, contribution: float) -> None:
        local = stamp.astimezone(EASTERN)
        if not (self._audit_from <= local.hour * 60 + local.minute < self._audit_to):
            return
        if len(self._audit_rows) >= MAX_AUDIT_ROWS:
            self.audit_dropped += 1
            return
        self._audit_rows.append(
            {
                "t": local.isoformat(timespec="milliseconds"),
                "symbol": event.symbol,
                "occ": event.occ_symbol,
                "size": event.size,
                "price": round(float(event.premium / (event.size * 100)), 4) if event.size > 0 else None,
                "bid": None if quote is None else float(quote[0]),
                "ask": None if quote is None else float(quote[1]),
                "condition": event.condition,
                "outcome": outcome,
                "abs_delta": delta,
                "contribution": round(contribution, 4),
            }
        )

    @property
    def symbols(self) -> frozenset[str]:
        return self._symbols

    def observe_quote(self, event: QuoteEvent) -> None:
        if event.symbol in self._symbols:
            self._quotes[event.occ_symbol] = (event.bid, event.ask)

    def set_deltas(self, symbol_deltas: dict[str, float]) -> None:
        """Adds (or refreshes) the |delta| of the contracts of one underlying; the processor calls it once a minute per underlying."""
        self._deltas.update(symbol_deltas)

    def observe_trade(self, event: FlowEvent, exchange_time: datetime | None = None) -> None:
        symbol = event.symbol
        if symbol not in self._symbols:
            return
        occ = event.occ_symbol
        meta = self._meta.get(occ)
        if meta is None:
            meta = self._meta[occ] = _root_and_right(occ)
        stamp = exchange_time if exchange_time is not None else event.as_of
        minute = int(stamp.timestamp()) // 60
        key = (minute, symbol, meta[0])
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = [0.0, 0.0, 0, 0, 0, 0]
            if len(self._buckets) > MAX_PENDING_BUCKETS:
                for old in sorted(self._buckets)[: len(self._buckets) - MAX_PENDING_BUCKETS]:
                    del self._buckets[old]
        size = event.size
        bucket[_CONTRACTS_ALL] += size
        audit = bool(self._audit_symbols) and symbol in self._audit_symbols
        cell = self._strike_cell(minute, symbol, occ, meta[1]) if self._strikes_on else None
        if event.condition != SIMPLE_TRADE_CONDITION:
            if cell is not None:
                cell[_PACKAGE if event.condition in MULTI_LEG_CONDITIONS else _OTHER] += size
            bucket[_TRADES_NO] += 1
            if audit:
                self._audit(stamp, event, self._quotes.get(occ), self._deltas.get(occ), "other_condition", 0.0)
            return
        quote = self._quotes.get(occ)
        delta = self._deltas.get(occ)
        if quote is None or delta is None or size <= 0:
            if cell is not None:
                cell[_OTHER] += size
            bucket[_TRADES_NO] += 1
            if audit:
                self._audit(stamp, event, quote, delta, "no_quote" if quote is None else "no_delta", 0.0)
            return
        price = event.premium / (size * 100)
        if price >= quote[1]:
            sign = 1.0
        elif price <= quote[0]:
            sign = -1.0
        else:
            if cell is not None:
                cell[_OTHER] += size
            bucket[_TRADES_NO] += 1
            if audit:
                self._audit(stamp, event, quote, delta, "inside_quote", 0.0)
            return
        flow = sign * size * delta
        if meta[1] == "C":
            bucket[_CALLS] += flow
        else:
            bucket[_PUTS] -= flow
        if cell is not None:
            cell[_FLOW] += flow if meta[1] == "C" else -flow
            cell[_SIGNED] += size
            cell[_BUY if sign > 0 else _SELL] += float(event.premium)
        if audit:
            self._audit(stamp, event, quote, delta, "buy" if sign > 0 else "sell", flow if meta[1] == "C" else -flow)
        bucket[_TRADES_OK] += 1
        bucket[_CONTRACTS_OK] += size

    def take_before(self, epoch_seconds: float | None = None) -> list[MinuteOptionFlow]:
        """Hands over every minute strictly before the minute that contains `epoch_seconds` (now by default) and forgets it."""
        now_minute = int(time.time() if epoch_seconds is None else epoch_seconds) // 60
        ready = [key for key in self._buckets if key[0] < now_minute]
        rows: list[MinuteOptionFlow] = []
        for key in sorted(ready):
            values = self._buckets.pop(key)
            minute_start = datetime.fromtimestamp(key[0] * 60, tz=EASTERN)
            rows.append(
                MinuteOptionFlow(
                    minute=minute_start,
                    symbol=key[1],
                    root=key[2],
                    calls_delta_flow=values[_CALLS],
                    puts_delta_flow=values[_PUTS],
                    trades_classified=int(values[_TRADES_OK]),
                    trades_unclassified=int(values[_TRADES_NO]),
                    contracts_classified=int(values[_CONTRACTS_OK]),
                    contracts_total=int(values[_CONTRACTS_ALL]),
                )
            )
        return rows

    def put_back(self, rows: list[MinuteOptionFlow]) -> None:
        """A failed flush returns its minutes (added to anything captured for the same minute meanwhile)."""
        for row in rows:
            key = (int(row.minute.timestamp()) // 60, row.symbol, row.root)
            bucket = self._buckets.setdefault(key, [0.0, 0.0, 0, 0, 0, 0])
            bucket[_CALLS] += row.calls_delta_flow
            bucket[_PUTS] += row.puts_delta_flow
            bucket[_TRADES_OK] += row.trades_classified
            bucket[_TRADES_NO] += row.trades_unclassified
            bucket[_CONTRACTS_OK] += row.contracts_classified
            bucket[_CONTRACTS_ALL] += row.contracts_total

    def take_strikes_before(self, epoch_seconds: float | None = None) -> list[MinuteStrikeFlow]:
        """Same as take_before for the per-strike cells."""
        now_minute = int(time.time() if epoch_seconds is None else epoch_seconds) // 60
        ready = [key for key in self._strike_cells if key[0] < now_minute]
        rows: list[MinuteStrikeFlow] = []
        for key in sorted(ready):
            cell = self._strike_cells.pop(key)
            rows.append(
                MinuteStrikeFlow(
                    minute=datetime.fromtimestamp(key[0] * 60, tz=EASTERN),
                    symbol=key[1],
                    expiration=key[2],
                    strike=key[3],
                    option_right=key[4],
                    delta_flow=cell[_FLOW],
                    contracts_signed=int(cell[_SIGNED]),
                    contracts_package=int(cell[_PACKAGE]),
                    contracts_other=int(cell[_OTHER]),
                    buy_premium=round(cell[_BUY], 2),
                    sell_premium=round(cell[_SELL], 2),
                )
            )
        return rows

    def put_back_strikes(self, rows: list[MinuteStrikeFlow]) -> None:
        """A failed flush returns its cells (added to anything captured for the same cell meanwhile)."""
        for row in rows:
            key = (int(row.minute.timestamp()) // 60, row.symbol, row.expiration, row.strike, row.option_right)
            cell = self._strike_cells.setdefault(key, [0.0, 0, 0, 0, 0.0, 0.0])
            cell[_FLOW] += row.delta_flow
            cell[_SIGNED] += row.contracts_signed
            cell[_PACKAGE] += row.contracts_package
            cell[_OTHER] += row.contracts_other
            cell[_BUY] += row.buy_premium
            cell[_SELL] += row.sell_premium
