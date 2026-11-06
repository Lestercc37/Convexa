"""Order-flow events detected in Lester's NinjaTrader on ES / NQ / RTY, translated to the index charts (2026-11-06).

Lester's plan (2026-11-06): the futures charts live in NinjaTrader, which reads Convexa's levels and heatmap ('Convexa Niveles'); the
'Convexa Detector' indicator (docs/ninjatrader/ConvexaDetector.cs) finds absorption, stacked imbalances and big trades on the futures tape and
sends ONLY those events here (POST /api/v1/feeds/futures-events). Convexa stores each one with its price translated to the instruments it
shows: ES -> SPX and SPY, NQ -> NDX and QQQ, RTY -> IWM. The raw CME tape never leaves NinjaTrader.

Imbalance rule (no rigorous public evidence exists; the common convention, docs/diseno-es-nq-en-vivo-ninjatrader-2026-11-06.txt): diagonal
footprint imbalance at 300 % (buys at a price >= 3x sells one tick below, or the mirror), 3 or more stacked levels, a minimum volume per level.
Absorption: aggressive selling into the best bid (or buying into the best ask) above a size while the bid keeps refilling and price does not
go through it. Every event is stored so that, after ~20 sessions, the price reaction after events near Convexa's bands can be measured against
events away from them (the same 'judge levels by price reaction' rule as every other study). Pure logic, no I/O."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

EVENT_TYPES = ("absorption", "stacked_imbalance", "big_trade")
SIDES = ("buy", "sell")
# The instruments each future's events are drawn on, and how the price is translated: the index is the future minus the basis; the ETF is the
# index divided by the index / ETF ratio of that moment. RTY has no index in Convexa: IWM = RTY / the live RTY / IWM ratio.
TARGETS: dict[str, tuple[str, ...]] = {"ES": ("SPX", "SPY"), "NQ": ("NDX", "QQQ"), "RTY": ("IWM",)}
INDEX_OF: dict[str, str] = {"ES": "SPX", "NQ": "NDX"}
ETF_OF_INDEX: dict[str, str] = {"SPX": "SPY", "NDX": "QQQ"}


@dataclass(frozen=True, slots=True)
class FuturesEvent:
    future: str
    contract: str
    at: datetime
    type: str
    side: str
    price: Decimal  # the futures price of the event (absorption: the absorbed price; stacked: the middle of the stack)
    price_low: Decimal
    price_high: Decimal
    volume: int  # contracts behind the event
    detail: str  # short free text from NinjaTrader, e.g. "refills 7, held 64 s" / "4 levels 312%"
    translated: dict[str, Decimal]  # symbol -> price on that symbol's chart
    received_at: datetime


def event_params(event: FuturesEvent) -> dict[str, object]:
    return {
        "future": event.future, "contract": event.contract, "at": event.at, "type": event.type, "side": event.side, "price": event.price,
        "price_low": event.price_low, "price_high": event.price_high, "volume": event.volume, "detail": event.detail,
        "translated": json.dumps({symbol: str(value) for symbol, value in event.translated.items()}), "received_at": event.received_at,
    }


def event_from_row(row) -> FuturesEvent:
    raw = row["translated"]
    translated = raw if isinstance(raw, dict) else json.loads(raw)
    return FuturesEvent(
        str(row["future"]), str(row["contract"]), row["at"], str(row["type"]), str(row["side"]), Decimal(str(row["price"])),
        Decimal(str(row["price_low"])), Decimal(str(row["price_high"])), int(row["volume"]), str(row["detail"]),
        {symbol: Decimal(str(value)) for symbol, value in translated.items()}, row["received_at"],
    )


def translate(future: str, price: Decimal, basis: Decimal | None, index_etf_ratio: float | None, rty_ratio: float | None) -> dict[str, Decimal]:
    """The event's price on each target chart, leaving out what cannot be translated right now (no basis yet, no ratio)."""
    out: dict[str, Decimal] = {}
    if future == "RTY":
        if rty_ratio:
            out["IWM"] = (price / Decimal(str(rty_ratio))).quantize(Decimal("0.01"))
        return out
    index = INDEX_OF.get(future)
    if index is None or basis is None:
        return out
    index_price = price - basis
    out[index] = index_price.quantize(Decimal("0.01"))
    if index_etf_ratio:
        out[ETF_OF_INDEX[index]] = (index_price / Decimal(str(index_etf_ratio))).quantize(Decimal("0.01"))
    return out


def future_for_symbol(symbol: str) -> str | None:
    """The future whose events are drawn on `symbol`'s chart (SPX / SPY -> ES, NDX / QQQ -> NQ, IWM -> RTY)."""
    symbol = symbol.upper()
    for future, targets in TARGETS.items():
        if symbol in targets:
            return future
    return None
