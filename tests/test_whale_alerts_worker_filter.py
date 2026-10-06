"""backend/whale_alerts_worker.py's _ActiveSymbolsRelayDataProvider: messages for symbols outside
ACTIVE_UNDERLYINGS are dropped before decoding/queueing, silently (one INFO summary at most every 60 s)."""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import backend.core.whale_alerts_relay as relay_module
import backend.whale_alerts_worker as worker_module
from backend.adapters.providers.thetadata.stream_parsing import parse_option_trade_message, parse_quote_message
from backend.core import fastjson
from backend.core.container import build_container
from backend.core.whale_alerts_relay import RELAY_QUEUE_MAXSIZE, RelayDataProvider, _encode_quote, _encode_trade
from backend.core.whale_alerts_stream import WhaleAlertsStreamManager
from backend.domain.entities import FlowEvent, FlowEventType, QuoteEvent, Side
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.whale_alerts_worker import (
    IGNORED_SYMBOLS_SUMMARY_INTERVAL_SECONDS,
    MISSING_SYMBOL_KEY,
    _ActiveSymbolsRelayDataProvider,
    _build_relay_provider,
)

NOW = datetime(2026, 10, 6, 13, 35, tzinfo=UTC)


def _trade_payload(symbol: str, occ: str | None = None) -> dict:
    event = FlowEvent(
        symbol=symbol,
        occ_symbol=occ or f"{symbol}261006C00100000",
        as_of=NOW,
        event_type=FlowEventType.UNUSUAL,
        premium=Decimal("1500.00"),
        size=3,
        aggressor_side=Side.UNKNOWN,
    )
    return fastjson.loads(_encode_trade(event))


def _quote_payload(symbol: str, occ: str | None = None) -> dict:
    event = QuoteEvent(
        symbol=symbol,
        occ_symbol=occ or f"{symbol}261006C00100000",
        as_of=NOW,
        bid=Decimal("1.10"),
        ask=Decimal("1.20"),
    )
    return fastjson.loads(_encode_quote(event))


def _provider(**kwargs) -> _ActiveSymbolsRelayDataProvider:
    return _ActiveSymbolsRelayDataProvider("127.0.0.1", 0, **kwargs)


# --- C.1: active symbols reach the consumer, removed ones never create a queue

@pytest.mark.asyncio
async def test_active_symbol_messages_are_queued_and_reach_the_consumer() -> None:
    provider = _provider()
    provider._dispatch(_trade_payload("SPX", "SPXW261006C07700000"))
    provider._dispatch(_quote_payload("SPX", "SPXW261006C07700000"))

    trade = await asyncio.wait_for(provider.stream_trades("SPX").__anext__(), timeout=1)
    quote = await asyncio.wait_for(provider.stream_quotes("SPX").__anext__(), timeout=1)

    assert trade.symbol == "SPX" and trade.occ_symbol == "SPXW261006C07700000" and trade.premium == Decimal("1500.00")
    assert quote.symbol == "SPX" and quote.bid == Decimal("1.10") and quote.ask == Decimal("1.20")
    assert sum(provider.ignored_by_symbol.values()) == 0


@pytest.mark.parametrize("symbol", ["TSLA", "META", "AMZN", "GOOGL", "AAPL", "MSFT", "DIA"])
def test_removed_symbols_create_no_queue_are_not_decoded_and_log_nothing(
    symbol: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("a message for an inactive symbol must not be decoded")

    monkeypatch.setattr(relay_module, "_decode_trade", explode)
    monkeypatch.setattr(relay_module, "_decode_quote", explode)
    provider = _provider()
    caplog.set_level(logging.DEBUG)

    for _ in range(RELAY_QUEUE_MAXSIZE + 1000):  # more than a queue holds: the old behavior logged CRITICAL here
        provider._dispatch(_trade_payload(symbol))
        provider._dispatch(_quote_payload(symbol))

    assert symbol not in provider._trade_queues and symbol not in provider._quote_queues
    assert provider.ignored_by_symbol[symbol] == 2 * (RELAY_QUEUE_MAXSIZE + 1000)
    assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []
    assert len(caplog.records) <= 1  # at most the INFO summary, never one line per message


def test_the_unfiltered_provider_really_does_fill_up_and_log_critical_for_a_removed_symbol(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Baseline for the test above: this is what the filter prevents."""
    provider = RelayDataProvider("127.0.0.1", 0)
    caplog.set_level(logging.CRITICAL)
    for _ in range(RELAY_QUEUE_MAXSIZE + 5):
        provider._dispatch(_trade_payload("TSLA"))
    assert provider._trade_queues["TSLA"].full()
    assert len([r for r in caplog.records if r.levelno == logging.CRITICAL]) == 5


# --- symbol format on the wire: the logical underlying, not the raw contract root

def _option_frame(root: str, kind: str) -> dict:
    contract = {"security_type": "OPTION", "root": root, "expiration": 20261006, "strike": 7700000, "right": "C"}
    if kind == "TRADE":
        return {"header": {"type": "TRADE"}, "contract": contract,
                "trade": {"price": 12.5, "size": 4, "date": 20261006, "ms_of_day": 34_500_000}}
    return {"header": {"type": "QUOTE"}, "contract": contract,
            "quote": {"bid": 12.0, "ask": 13.0, "date": 20261006, "ms_of_day": 34_500_000}}


@pytest.mark.parametrize(
    ("root", "expected_symbol"),
    [("SPXW", "SPX"), ("NDXP", "NDX"), ("VIXW", "VIX"), ("SPY", "SPY"), ("QQQ", "QQQ"), ("IWM", "IWM"),
     ("NVDA", "NVDA"), ("ES", "ES"), ("NQ", "NQ")],
)
def test_real_parsed_wire_messages_for_every_active_root_pass_the_filter(root: str, expected_symbol: str) -> None:
    provider = _provider()
    trade = parse_option_trade_message(_option_frame(root, "TRADE"))
    quote = parse_quote_message(_option_frame(root, "QUOTE"))
    assert trade is not None and trade.event is not None and quote is not None

    trade_payload = fastjson.loads(_encode_trade(trade.event))
    quote_payload = fastjson.loads(_encode_quote(quote.event))
    assert trade_payload["symbol"] == quote_payload["symbol"] == expected_symbol  # logical underlying

    provider._dispatch(trade_payload)
    provider._dispatch(quote_payload)
    assert expected_symbol in provider._trade_queues and expected_symbol in provider._quote_queues
    assert sum(provider.ignored_by_symbol.values()) == 0


# --- C.2: malformed payloads are dropped without an exception or a log line

@pytest.mark.parametrize(
    "payload",
    [{"k": "t"}, {"k": "t", "symbol": None}, {"k": "q", "symbol": ""}, {"k": "t", "symbol": 7},
     {"k": "t", "symbol": ["SPX"]}, {"k": "t", "symbol": {"a": 1}}, {}, [], 5, None, "SPX"],
)
def test_payloads_without_a_usable_symbol_are_dropped_silently(
    payload: object, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _provider()
    caplog.set_level(logging.DEBUG)
    provider._dispatch(payload)  # type: ignore[arg-type]
    assert provider._trade_queues == {} and provider._quote_queues == {}
    assert sum(provider.ignored_by_symbol.values()) == 1
    assert caplog.records == []


def test_a_symbol_in_a_different_case_is_not_active_because_the_consumers_use_exact_uppercase_symbols() -> None:
    provider = _provider()
    provider._dispatch(_trade_payload("spx"))
    assert provider._trade_queues == {} and provider.ignored_by_symbol["spx"] == 1


# --- C.3 / B.2: the filter tracks ACTIVE_UNDERLYINGS and exactly what the manager consumes

def test_every_active_underlying_passes_the_filter() -> None:
    provider = _provider()
    assert provider.active_symbols == {underlying.symbol for underlying in ACTIVE_UNDERLYINGS}
    for underlying in ACTIVE_UNDERLYINGS:
        provider._dispatch(_trade_payload(underlying.symbol))
        provider._dispatch(_quote_payload(underlying.symbol))
        assert underlying.symbol in provider._trade_queues and underlying.symbol in provider._quote_queues
    assert sum(provider.ignored_by_symbol.values()) == 0


def test_a_symbol_added_to_active_underlyings_later_is_tracked_without_touching_the_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.domain.entities import Underlying, UnderlyingKind

    monkeypatch.setattr(worker_module, "ACTIVE_UNDERLYINGS", (*ACTIVE_UNDERLYINGS, Underlying("XYZ", UnderlyingKind.EQUITY, True)))
    provider = _provider()
    assert "XYZ" in provider.active_symbols
    provider._dispatch(_trade_payload("XYZ"))
    assert "XYZ" in provider._trade_queues


@pytest.mark.asyncio
async def test_the_filter_covers_exactly_the_symbols_the_stream_manager_consumes() -> None:
    consumed: list[str] = []

    class _Recorder:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run(self, underlying: str) -> None:
            consumed.append(underlying)

    container = build_container()
    try:
        import backend.core.whale_alerts_stream as stream_module

        original = stream_module.StreamWhaleAlertsUseCase
        stream_module.StreamWhaleAlertsUseCase = _Recorder  # type: ignore[misc,assignment]
        try:
            manager = WhaleAlertsStreamManager(container)
            manager.start()
            await asyncio.sleep(0.1)
            await manager.stop()
        finally:
            stream_module.StreamWhaleAlertsUseCase = original  # type: ignore[misc]
    finally:
        if container.storage_engine is not None:
            container.storage_engine.dispose()
        await container.database_engine.dispose()

    assert len(consumed) == len(ACTIVE_UNDERLYINGS) == 9
    assert set(consumed) == _provider().active_symbols


# --- D: observability, no per-message logging

def test_ignored_messages_are_summarized_once_per_interval_and_only_when_there_are_any(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = {"t": 1000.0}
    provider = _provider(monotonic=lambda: clock["t"])
    caplog.set_level(logging.INFO, logger=worker_module.logger.name)

    for _ in range(5):
        provider._dispatch(_trade_payload("TSLA"))
    provider._dispatch(_quote_payload("META"))
    assert caplog.records == []  # inside the interval: nothing logged

    clock["t"] += IGNORED_SYMBOLS_SUMMARY_INTERVAL_SECONDS + 1
    provider._dispatch(_trade_payload("TSLA"))  # first ignored message after the interval triggers the summary
    summaries = [r for r in caplog.records if "ignored" in r.getMessage()]
    assert len(summaries) == 1 and summaries[0].levelno == logging.INFO
    assert "TSLA=6" in summaries[0].getMessage() and "META=1" in summaries[0].getMessage()
    assert "7 since start" in summaries[0].getMessage()

    # Next interval: only active traffic -> no line at all.
    caplog.clear()
    clock["t"] += 3 * IGNORED_SYMBOLS_SUMMARY_INTERVAL_SECONDS
    provider._dispatch(_trade_payload("SPX"))
    assert caplog.records == []
    assert provider.ignored_by_symbol["TSLA"] == 6 and provider.ignored_by_symbol["META"] == 1


def test_missing_symbols_are_counted_under_a_placeholder_key() -> None:
    provider = _provider()
    provider._dispatch({"k": "t"})
    assert provider.ignored_by_symbol[MISSING_SYMBOL_KEY] == 1


# --- A.3: the entrypoint builds the filtered provider and nothing else changed there

def test_the_entrypoint_uses_the_filtered_provider() -> None:
    assert isinstance(_build_relay_provider("127.0.0.1", 0), _ActiveSymbolsRelayDataProvider)
    source = inspect.getsource(worker_module.run)
    assert "_build_relay_provider(" in source and "RelayDataProvider(" not in source.replace("_build_relay_provider(", "")


def test_the_filter_does_not_change_the_relay_module_constants() -> None:
    assert RELAY_QUEUE_MAXSIZE == 20000
