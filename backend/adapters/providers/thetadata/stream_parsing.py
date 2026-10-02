"""Pure ThetaData stream message shape helpers and QUOTE/TRADE parsing.

Moved out of provider.py 2026-10-02 (root/symbol helpers) and extracted
new (the parse_*_message functions) so the exact same classification
logic can run either inline in ThetaStreamHub (when no stream processor
process is connected -- see provider.py's own _handle_quote/
_handle_option_trade/_handle_underlying_trade, now thin wrappers around
these) or out-of-process (backend/stream_processor_worker.py, the new
split -- see StreamProcessorRelayServer's own docstring for why this had
to move: a single asyncio event loop cannot give the WS read loop and
this parsing/dispatch work genuine parallelism, only cooperative
interleaving, and this work was measured taking 100-150ms per message
under real volume, enough on its own to stall the read loop for that
whole span).

No side effects, no `self`, no queue access, no logging in the parse_*
functions below -- each takes a raw message dict (plus whatever registry
state it genuinely needs) and returns a plain result the caller decides
what to do with. This is what makes running the identical logic in a
different OS process possible: nothing here reaches back into
ThetaStreamHub's own state. provider.py imports every name below rather
than redefining it, so `from backend.adapters.providers.thetadata.provider
import _build_occ_symbol` (existing tests, written before this split)
keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as time_of_day
from decimal import Decimal
from typing import Any

from backend.domain.entities import (
    ContractType,
    FlowEvent,
    FlowEventType,
    QuoteEvent,
    Side,
    UnderlyingKind,
    UnderlyingTradeEvent,
    utc_now,
)
from backend.domain.use_cases.market_hours import EASTERN_TIME

# SPX/NDX/VIX each have a real, separately-traded weekly root
# (SPXW/NDXP/VIXW) -- see _roots_for_symbol's own original comment in
# provider.py's git history for the full investigation. Kept here, not
# duplicated, because _underlying_symbol_for_root (the reverse direction,
# used on every incoming stream message) needs it and must never drift
# out of sync with the forward direction (_roots_for_symbol, still in
# provider.py -- it's also used by plain REST fetches that have nothing
# to do with stream parsing, so it stays where the REST code lives).
_WEEKLY_ROOT_BY_SYMBOL: dict[str, str] = {
    "SPX": "SPXW",
    "NDX": "NDXP",
    "VIX": "VIXW",
}

# Reverse of _WEEKLY_ROOT_BY_SYMBOL -- confirmed live, 2026-09-25: every
# incoming trade/quote message's own `contract.root` field is whichever
# specific root the contract actually trades under (e.g. "SPXW" for a
# near-dated/0DTE SPX contract, not "SPX" itself). Dispatching by the raw
# message root instead of resolving it back through this map means every
# SPXW/NDXP/VIXW trade and quote silently reaches zero subscribers.
_SYMBOL_BY_WEEKLY_ROOT: dict[str, str] = {
    weekly_root: symbol for symbol, weekly_root in _WEEKLY_ROOT_BY_SYMBOL.items()
}


def _underlying_symbol_for_root(root: str) -> str:
    """Resolves a raw ThetaData contract root (already upper-cased, e.g.
    "SPXW") back to the logical underlying symbol subscribers key on
    (e.g. "SPX"). A plain pass-through for every symbol without a
    weekly-root split (SPY, QQQ, AAPL, ...) -- their own root already IS
    the logical symbol."""
    return _SYMBOL_BY_WEEKLY_ROOT.get(root, root)


def _build_occ_symbol(
    root: str, expiration: date, contract_type: ContractType, strike: Decimal
) -> str:
    suffix = "C" if contract_type == ContractType.CALL else "P"
    return f"{root}{expiration:%y%m%d}{suffix}{int(strike * 1000):08d}"


def _parse_stream_tick_timestamp(date_int: int, ms_of_day: int) -> datetime:
    """The WS QUOTE/TRADE stream's own `date` (YYYYMMDD) + `ms_of_day`
    (milliseconds since midnight ET) fields, combined into the real
    exchange-side timestamp of that tick -- not the REST snapshot
    endpoints' `timestamp` string field (`_parse_et_timestamp` in
    provider.py handles that one). Added 2026-10-02 to measure consumer
    lag (now() minus this) directly, per Eduardo/ThetaData support's
    point: on a loopback connection a 1011 keepalive-ping-timeout
    specifically indicates OUR reader fell behind live messages, not a
    remote network/server issue."""
    date_digits = str(date_int)
    tick_date = date(int(date_digits[:4]), int(date_digits[4:6]), int(date_digits[6:8]))
    midnight = datetime.combine(tick_date, time_of_day(0, 0), tzinfo=EASTERN_TIME)
    return midnight + timedelta(milliseconds=ms_of_day)


@dataclass(frozen=True)
class ParsedQuote:
    event: QuoteEvent
    underlying_symbol: str
    exchange_ts: datetime | None


@dataclass(frozen=True)
class ParsedOptionTrade:
    occ_symbol: str
    size: int
    underlying_symbol: str
    exchange_ts: datetime | None
    # None when the message had no `price` -- volume still counts (see
    # the original _handle_option_trade's own comment), there's just
    # nothing dispatchable to Whale Alerts/the chart for this one.
    event: FlowEvent | None


@dataclass(frozen=True)
class ParsedUnderlyingTrade:
    event: UnderlyingTradeEvent
    exchange_ts: datetime | None


def parse_quote_message(message: dict[str, Any]) -> ParsedQuote | None:
    contract = message.get("contract", {})
    quote = message.get("quote", {})
    root = contract.get("root")
    expiration_raw = contract.get("expiration")
    strike_raw = contract.get("strike")
    right = contract.get("right")
    bid = quote.get("bid")
    ask = quote.get("ask")
    if root is None or expiration_raw is None or strike_raw is None or bid is None or ask is None:
        return None
    quote_date = quote.get("date")
    quote_ms_of_day = quote.get("ms_of_day")
    exchange_ts = (
        _parse_stream_tick_timestamp(quote_date, quote_ms_of_day)
        if quote_date is not None and quote_ms_of_day is not None
        else None
    )
    expiration_digits = str(expiration_raw)
    expiration = date(
        int(expiration_digits[:4]), int(expiration_digits[4:6]), int(expiration_digits[6:8])
    )
    contract_type = ContractType.CALL if right == "C" else ContractType.PUT
    strike = Decimal(strike_raw) / Decimal(1000)
    occ_symbol = _build_occ_symbol(root, expiration, contract_type, strike)
    underlying_symbol = _underlying_symbol_for_root(root.upper())
    return ParsedQuote(
        event=QuoteEvent(
            symbol=underlying_symbol,
            occ_symbol=occ_symbol,
            as_of=utc_now(),
            bid=Decimal(str(bid)),
            ask=Decimal(str(ask)),
        ),
        underlying_symbol=underlying_symbol,
        exchange_ts=exchange_ts,
    )


def parse_option_trade_message(message: dict[str, Any]) -> ParsedOptionTrade | None:
    contract = message.get("contract", {})
    trade = message.get("trade", {})
    root = contract.get("root")
    expiration_raw = contract.get("expiration")
    strike_raw = contract.get("strike")
    right = contract.get("right")
    size = trade.get("size")
    if root is None or expiration_raw is None or strike_raw is None or size is None:
        return None
    trade_date = trade.get("date")
    trade_ms_of_day = trade.get("ms_of_day")
    exchange_ts = (
        _parse_stream_tick_timestamp(trade_date, trade_ms_of_day)
        if trade_date is not None and trade_ms_of_day is not None
        else None
    )
    expiration_digits = str(expiration_raw)
    expiration = date(
        int(expiration_digits[:4]), int(expiration_digits[4:6]), int(expiration_digits[6:8])
    )
    contract_type = ContractType.CALL if right == "C" else ContractType.PUT
    strike = Decimal(strike_raw) / Decimal(1000)
    occ_symbol = _build_occ_symbol(root, expiration, contract_type, strike)
    underlying_symbol = _underlying_symbol_for_root(root.upper())
    price = trade.get("price")
    event = (
        FlowEvent(
            symbol=underlying_symbol,
            occ_symbol=occ_symbol,
            as_of=utc_now(),
            event_type=FlowEventType.UNUSUAL,
            premium=Decimal(str(price)) * Decimal(size) * Decimal(100),
            size=int(size),
            aggressor_side=Side.UNKNOWN,
        )
        if price is not None
        else None
    )
    return ParsedOptionTrade(
        occ_symbol=occ_symbol,
        size=int(size),
        underlying_symbol=underlying_symbol,
        exchange_ts=exchange_ts,
        event=event,
    )


def encode_parsed_event(
    parsed: ParsedQuote | ParsedOptionTrade | ParsedUnderlyingTrade,
) -> dict[str, Any]:
    """The wire format StreamProcessorRelayClient sends a classified
    result back to worker.py in (see backend/core/stream_processor_relay.py's
    own docstring) -- one dict per parsed_* result above, paired with
    decode_parsed_event below. Kept here, not in stream_processor_relay.py
    itself, so the encode/decode pair stays next to the dataclasses they
    serialize and can never drift out of sync with a field rename."""
    exchange_ts = (
        parsed.exchange_ts.isoformat() if parsed.exchange_ts is not None else None
    )
    if isinstance(parsed, ParsedQuote):
        return {
            "k": "quote",
            "exchange_ts": exchange_ts,
            "underlying_symbol": parsed.underlying_symbol,
            "symbol": parsed.event.symbol,
            "occ_symbol": parsed.event.occ_symbol,
            "bid": str(parsed.event.bid),
            "ask": str(parsed.event.ask),
        }
    if isinstance(parsed, ParsedOptionTrade):
        event = parsed.event
        return {
            "k": "option_trade",
            "exchange_ts": exchange_ts,
            "occ_symbol": parsed.occ_symbol,
            "size": parsed.size,
            "underlying_symbol": parsed.underlying_symbol,
            "event": (
                {
                    "symbol": event.symbol,
                    "occ_symbol": event.occ_symbol,
                    "premium": str(event.premium),
                    "size": event.size,
                }
                if event is not None
                else None
            ),
        }
    return {
        "k": "underlying_trade",
        "exchange_ts": exchange_ts,
        "symbol": parsed.event.symbol,
        "price": str(parsed.event.price),
        "size": parsed.event.size,
    }


def decode_parsed_event(
    payload: dict[str, Any],
) -> ParsedQuote | ParsedOptionTrade | ParsedUnderlyingTrade:
    """Inverse of encode_parsed_event -- see that function's own
    docstring. `as_of` on every reconstructed domain event is stamped
    fresh with utc_now() here (worker.py's own receipt time), same as
    the in-process path (_handle_quote/etc.) already does -- never the
    processor's own construction time, which would just be clock skew
    between two processes on the same machine for no benefit."""
    exchange_ts = (
        datetime.fromisoformat(payload["exchange_ts"]) if payload.get("exchange_ts") else None
    )
    kind = payload["k"]
    if kind == "quote":
        return ParsedQuote(
            event=QuoteEvent(
                symbol=payload["symbol"],
                occ_symbol=payload["occ_symbol"],
                as_of=utc_now(),
                bid=Decimal(payload["bid"]),
                ask=Decimal(payload["ask"]),
            ),
            underlying_symbol=payload["underlying_symbol"],
            exchange_ts=exchange_ts,
        )
    if kind == "option_trade":
        raw_event = payload.get("event")
        return ParsedOptionTrade(
            occ_symbol=payload["occ_symbol"],
            size=payload["size"],
            underlying_symbol=payload["underlying_symbol"],
            exchange_ts=exchange_ts,
            event=(
                FlowEvent(
                    symbol=raw_event["symbol"],
                    occ_symbol=raw_event["occ_symbol"],
                    as_of=utc_now(),
                    event_type=FlowEventType.UNUSUAL,
                    premium=Decimal(raw_event["premium"]),
                    size=raw_event["size"],
                    aggressor_side=Side.UNKNOWN,
                )
                if raw_event is not None
                else None
            ),
        )
    return ParsedUnderlyingTrade(
        event=UnderlyingTradeEvent(
            symbol=payload["symbol"],
            as_of=utc_now(),
            price=Decimal(payload["price"]),
            size=payload["size"],
        ),
        exchange_ts=exchange_ts,
    )


def parse_underlying_trade_message(
    message: dict[str, Any], symbols: dict[str, UnderlyingKind]
) -> ParsedUnderlyingTrade | None:
    contract = message.get("contract", {})
    trade = message.get("trade", {})
    root = contract.get("root")
    price = trade.get("price")
    size = trade.get("size")
    if root is None or price is None or size is None:
        return None
    trade_date = trade.get("date")
    trade_ms_of_day = trade.get("ms_of_day")
    exchange_ts = (
        _parse_stream_tick_timestamp(trade_date, trade_ms_of_day)
        if trade_date is not None and trade_ms_of_day is not None
        else None
    )
    symbol = root.upper()
    # See the original _handle_underlying_trade's own comment (now this
    # function's caller in provider.py) for the 2026-09-03 incident this
    # guard fixed -- an OPTION trade sharing this root (e.g. a VIX
    # call/put) leaking in and getting published as if it were the
    # underlying's own price.
    kind = symbols.get(symbol)
    if kind is None:
        return None
    expected_security_type = "INDEX" if kind == UnderlyingKind.INDEX else "STOCK"
    if contract.get("security_type") != expected_security_type:
        return None
    return ParsedUnderlyingTrade(
        event=UnderlyingTradeEvent(
            symbol=symbol,
            as_of=utc_now(),
            price=Decimal(str(price)),
            size=int(size),
        ),
        exchange_ts=exchange_ts,
    )
