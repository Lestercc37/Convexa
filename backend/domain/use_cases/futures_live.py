"""Live ES / NQ / RTY from Lester's NinjaTrader (docs/diseno-es-nq-en-vivo-ninjatrader-2026-11-06.txt, phase 2; Lester "sigue con la fase 2" 2026-11-06).

The 'Convexa Feed' indicator (docs/ninjatrader/ConvexaFeed.cs) posts, once a second, the last price of each of his 1-minute ES / NQ / RTY
(or MES / MNQ / M2K) charts and every closed 1-minute bar (POST /api/v1/feeds/futures-live). Stored in futures_live_quotes (latest per symbol)
and futures_live_bars (one row per minute; migration 0055). What it is used for:
- the LIVE BASIS: each regular-hours bar's close minus the index's last price inside that minute (SPX for ES, NDX for NQ). The median of the
  last LIVE_BASIS_BARS fresh bars replaces the fixed morning offset (anchor - the index's first minute) in futures_proxy.future_level_offset,
  so ES / NQ prices, levels and heatmap follow the real futures instead of a number frozen at 09:31. On 2026-11-06 the anchor offset was
  +26.0 while the real basis was ~+31 (ThetaData's SPX was already off in the first minute);
- the real last price, shown ONLY to the owner (CME real-time data is for his personal use; teammates keep the SPX / NDX + basis estimate).
RTY (Russell 2000) is stored with IWM as its index (an ETF ~1/10 of the index, so a RATIO, not a difference): kept for a future RTY chart.
A minute whose index price was itself replaced (index_stale_guard active) never makes a basis: that would be a basis against a stale index.
Pure logic, no I/O."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from backend.domain.use_cases.futures_anchor import contract_expiry

# future -> the symbol its basis is measured against
LIVE_FUTURES: dict[str, str] = {"ES": "SPX", "NQ": "NDX", "RTY": "IWM"}
MICRO_TO_FULL: dict[str, str] = {"MES": "ES", "MNQ": "NQ", "M2K": "RTY"}
LIVE_BASIS_BARS = 5
# The newest bar used for the basis must be at most this old; older -> the morning anchor again (NinjaTrader closed, PC asleep).
LIVE_BASIS_FRESH = timedelta(minutes=10)
# A live basis this far from the anchor offset is not trusted (a wrong chart, a bad print): the anchor stays.
LIVE_BASIS_MAX_GAP: dict[str, float] = {"ES": 25.0, "NQ": 100.0}
# The last price is "live" for this long; a quote whose NinjaTrader time is further than QUOTE_CLOCK_SKEW from the server's is not used.
QUOTE_FRESH = timedelta(seconds=10)
QUOTE_CLOCK_SKEW = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class FuturesLiveQuote:
    symbol: str
    contract: str
    last: Decimal
    bid: Decimal | None
    ask: Decimal | None
    exchange_time: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class FuturesLiveBar:
    symbol: str
    contract: str
    minute: datetime  # START of the 1-minute bar
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    session: str  # 'rth' (09:30-16:00 ET) or 'eth'
    index_price: Decimal | None  # the index's last price inside the same minute, None when it had none or was replaced
    received_at: datetime


def quote_params(quote: FuturesLiveQuote) -> dict[str, object]:
    return {
        "symbol": quote.symbol, "contract": quote.contract, "last": quote.last, "bid": quote.bid, "ask": quote.ask,
        "exchange_time": quote.exchange_time, "received_at": quote.received_at,
    }


def quote_from_row(row) -> FuturesLiveQuote:
    return FuturesLiveQuote(
        str(row["symbol"]), str(row["contract"]), Decimal(str(row["last"])),
        None if row["bid"] is None else Decimal(str(row["bid"])), None if row["ask"] is None else Decimal(str(row["ask"])),
        row["exchange_time"], row["received_at"],
    )


def bar_params(bar: FuturesLiveBar) -> dict[str, object]:
    return {
        "symbol": bar.symbol, "contract": bar.contract, "minute": bar.minute, "open": bar.open, "high": bar.high, "low": bar.low,
        "close": bar.close, "volume": bar.volume, "session": bar.session, "index_price": bar.index_price, "received_at": bar.received_at,
    }


def bar_from_row(row) -> FuturesLiveBar:
    return FuturesLiveBar(
        str(row["symbol"]), str(row["contract"]), row["minute"], Decimal(str(row["open"])), Decimal(str(row["high"])),
        Decimal(str(row["low"])), Decimal(str(row["close"])), int(row["volume"]), str(row["session"]),
        None if row["index_price"] is None else Decimal(str(row["index_price"])), row["received_at"],
    )


def normalize_live_future(name: str) -> str | None:
    """'ES' / 'MES' / 'ES 12-26' / 'M2K 12-26' -> 'ES' / 'RTY'; None for anything else."""
    root = name.strip().upper().split(" ")[0]
    root = MICRO_TO_FULL.get(root, root)
    return root if root in LIVE_FUTURES else None


MONTH_CODES = {name: number for number, name in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}


def contract_month(contract: str) -> tuple[int, int] | None:
    """'ES 12-26' or NinjaTrader's own 'ES DEC26' (what Instrument.FullName really sends, 2026-11-06) -> (2026, 12); None otherwise."""
    parts = contract.strip().upper().split(" ")
    if len(parts) < 2:
        return None
    tag = parts[1]
    if "-" in tag:
        month, _, year = tag.partition("-")
        return (2000 + int(year), int(month)) if month.isdigit() and year.isdigit() else None
    if len(tag) == 5 and tag[:3] in MONTH_CODES and tag[3:].isdigit():
        return 2000 + int(tag[3:]), MONTH_CODES[tag[:3]]
    return None


def is_front(contract: str, day: date) -> bool:
    """The quarterly contract that is front on `day` (the anchor's roll rule: 8 days before the third Friday). ES, NQ and RTY share the cycle."""
    expiry = contract_expiry(day)
    return contract_month(contract) == (expiry.year, expiry.month)


def live_basis(bars: list[FuturesLiveBar], now: datetime, fresh: timedelta | None = LIVE_BASIS_FRESH) -> tuple[float, int, datetime] | None:
    """(median close - index price, bars used, newest bar minute) of the last LIVE_BASIS_BARS front regular-hours bars with an index price,
    or None when the newest of them is older than `fresh` (None: any age, the LAST KNOWN basis). `bars` in any order."""
    usable = sorted(
        (bar for bar in bars if bar.index_price is not None and bar.session == "rth" and is_front(bar.contract, bar.minute.date())),
        key=lambda bar: bar.minute,
    )[-LIVE_BASIS_BARS:]
    if not usable or (fresh is not None and now - (usable[-1].minute + timedelta(minutes=1)) > fresh):
        return None
    return statistics.median(float(bar.close - bar.index_price) for bar in usable), len(usable), usable[-1].minute


def live_ratio(bars: list[FuturesLiveBar], now: datetime) -> tuple[float, int, datetime] | None:
    """Like live_basis but close / index price: RTY against IWM (an ETF about a tenth of the index, so a ratio, not a difference)."""
    usable = sorted(
        (bar for bar in bars if bar.index_price is not None and bar.index_price > 0 and bar.session == "rth" and is_front(bar.contract, bar.minute.date())),
        key=lambda bar: bar.minute,
    )[-LIVE_BASIS_BARS:]
    if not usable or now - (usable[-1].minute + timedelta(minutes=1)) > LIVE_BASIS_FRESH:
        return None
    return statistics.median(float(bar.close / bar.index_price) for bar in usable), len(usable), usable[-1].minute


def trusted_basis(future: str, live: float | None, anchor_offset: float | None) -> bool:
    """A live basis replaces the anchor offset unless both exist and they are further apart than LIVE_BASIS_MAX_GAP."""
    if live is None:
        return False
    gap = LIVE_BASIS_MAX_GAP.get(future)
    return anchor_offset is None or gap is None or abs(live - anchor_offset) <= gap


def is_rth(minute_start_et: datetime) -> bool:
    minutes = minute_start_et.hour * 60 + minute_start_et.minute
    return 9 * 60 + 30 <= minutes < 16 * 60
