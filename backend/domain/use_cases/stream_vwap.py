"""Shadow, per-trade session VWAP of the underlyings, from the stream's own STOCK trades (Lester 2026-10-27: "el VWAP se atrasa" on every symbol).

The anchored VWAP the dashboard draws is computed from market_snapshots, whose session volume only changes when the REST cycle writes it (about once a minute;
the stream's price ticks carry that number forward), so the line moves in one-minute steps and up to ~90 s behind the price once the page's 30 s poll is added.
The stream already delivers every trade of SPY / QQQ / IWM / NVDA with its price and size, so the VWAP can be accumulated trade by trade: sum(price x size) /
sum(size) over the regular session (09:30-16:00 ET by the exchange timestamp).

SHADOW ONLY: this module feeds a log line a minute (stream_processor_worker), nothing on screen or in the database reads it. It is compared against the current
anchored VWAP and an external one before anything switches (the anchored VWAP is one of Lester's protected levels)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from backend.domain.use_cases.market_hours import EASTERN_TIME

SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


@dataclass(slots=True)
class _Running:
    session: date
    price_volume: Decimal = Decimal(0)
    volume: int = 0
    trades: int = 0
    last_trade_at: datetime | None = None


class StreamVwap:
    def __init__(self) -> None:
        self._by_symbol: dict[str, _Running] = {}

    def observe(self, symbol: str, price: Decimal, size: int, exchange_ts: datetime | None) -> None:
        """One trade. Ignored without an exchange timestamp, without size (index ticks), or outside the regular session of its own ET day."""
        if exchange_ts is None or size <= 0 or price <= 0:
            return
        local = exchange_ts.astimezone(EASTERN_TIME)
        if not SESSION_OPEN <= local.time() < SESSION_CLOSE:
            return
        running = self._by_symbol.get(symbol)
        if running is None or running.session != local.date():
            running = _Running(session=local.date())
            self._by_symbol[symbol] = running
        running.price_volume += price * size
        running.volume += size
        running.trades += 1
        running.last_trade_at = local

    def value(self, symbol: str) -> Decimal | None:
        running = self._by_symbol.get(symbol)
        if running is None or running.volume == 0:
            return None
        return running.price_volume / running.volume

    def summary(self) -> str:
        """One line for the log: every symbol's VWAP, shares and trades so far, and the time of its last trade."""
        parts = []
        for symbol in sorted(self._by_symbol):
            running = self._by_symbol[symbol]
            vwap = self.value(symbol)
            if vwap is None:
                continue
            last = running.last_trade_at.strftime("%H:%M:%S") if running.last_trade_at else "-"
            parts.append(f"{symbol} {vwap:.4f} ({running.volume:,} sh, {running.trades:,} trades, last {last})")
        return "; ".join(parts)
