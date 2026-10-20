"""Signed option delta flow per ET minute, kept in memory by the stream processor and flushed once a minute (table minute_option_flow, migration 0048).

What it counts: a SIMPLE option trade (OPRA condition 18) whose price is at or beyond the prevailing bid/ask of its contract. A price at or above the ask is a buy, at or below the bid a sell;
anything else (inside the spread, no quote yet, no delta yet, another condition such as a multi-leg or an auction) is NOT given a side and is only counted as unclassified, so the share of the volume
that carries a side is always stored next to the flow. The size of a trade is contracts x |delta| (the delta of the contract in the last chain snapshot of the scheduler, refreshed once a minute by
the processor, never per trade): the same "delta-equivalent contracts" unit as the alert cubes. Convention: buying a call +, selling a call -, buying a put -, selling a put + (positive = bullish
pressure on both sides). It is an ESTIMATE of customer flow from the aggressor side: not inventory, not the market maker's position, not the hedge in futures.

Cost on the processor's hot path: a QUOTE that survived the repeat filter is one dict assignment (only for the configured underlyings); an option TRADE is a handful of dict lookups, one Decimal division
and one addition. The rows are built only at the minute flush, in a separate task.
"""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.domain.entities import FlowEvent, MinuteOptionFlow, QuoteEvent

EASTERN = ZoneInfo("America/New_York")
SIMPLE_TRADE_CONDITION = 18
# Default underlyings: the tradeable ones (indices, the four liquid ETFs, NVDA). VIX options are left out (their own expiries and quoting).
MINUTE_FLOW_SYMBOLS: frozenset[str] = frozenset({"SPX", "NDX", "SPY", "QQQ", "IWM", "NVDA"})
# A flush that keeps failing must not grow memory without bound: the oldest minutes go first.
MAX_PENDING_BUCKETS = 5_000

# bucket = [calls_delta_flow, puts_delta_flow, trades_classified, trades_unclassified, contracts_classified, contracts_total]
_CALLS, _PUTS, _TRADES_OK, _TRADES_NO, _CONTRACTS_OK, _CONTRACTS_ALL = range(6)


def _root_and_right(occ_symbol: str) -> tuple[str, str]:
    """('SPXW', 'C') from 'SPXW261019C07700000': the letters before the 6-digit expiration, and the right character before the 8-digit strike."""
    index = 0
    while index < len(occ_symbol) and not occ_symbol[index].isdigit():
        index += 1
    return occ_symbol[:index], occ_symbol[-9]


class MinuteFlowRecorder:
    def __init__(self, symbols: frozenset[str] = MINUTE_FLOW_SYMBOLS) -> None:
        self._symbols = symbols
        self._quotes: dict[str, tuple[Decimal, Decimal]] = {}
        self._deltas: dict[str, float] = {}
        self._meta: dict[str, tuple[str, str]] = {}
        self._buckets: dict[tuple[int, str, str], list[float]] = {}

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
        if event.condition != SIMPLE_TRADE_CONDITION:
            bucket[_TRADES_NO] += 1
            return
        quote = self._quotes.get(occ)
        delta = self._deltas.get(occ)
        if quote is None or delta is None or size <= 0:
            bucket[_TRADES_NO] += 1
            return
        price = event.premium / (size * 100)
        if price >= quote[1]:
            sign = 1.0
        elif price <= quote[0]:
            sign = -1.0
        else:
            bucket[_TRADES_NO] += 1
            return
        flow = sign * size * delta
        if meta[1] == "C":
            bucket[_CALLS] += flow
        else:
            bucket[_PUTS] -= flow
        bucket[_TRADES_OK] += 1
        bucket[_CONTRACTS_OK] += size

    def take_before(self, epoch_seconds: float | None = None) -> list[MinuteOptionFlow]:
        """Hands over every minute strictly before the minute that contains `epoch_seconds` (now by default) and forgets it."""
        now_minute = int((time.time() if epoch_seconds is None else epoch_seconds)) // 60
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
