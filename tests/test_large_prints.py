"""The large-print capture: SPY/QQQ prints over $1M kept with their venue and condition, marked at market or not (use_cases/large_prints.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from backend.adapters.providers.thetadata.stream_parsing import parse_underlying_trade_message
from backend.domain.entities import UnderlyingKind, UnderlyingTradeEvent
from backend.domain.use_cases.large_prints import MAX_PENDING_ROWS, LargePrintRecorder

NOW = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)


def _event(symbol: str, price: str, size: int, condition: int | None = 0, exchange: int | None = 57) -> UnderlyingTradeEvent:
    return UnderlyingTradeEvent(symbol=symbol, as_of=NOW, price=Decimal(price), size=size, condition=condition, exchange=exchange)


def test_only_prints_over_the_threshold_of_the_watched_symbols_are_kept() -> None:
    recorder = LargePrintRecorder()
    recorder.observe(_event("SPY", "770.00", 1000))  # $770k: below
    recorder.observe(_event("SPY", "770.00", 1300))  # $1.001M: kept
    recorder.observe(_event("AAPL", "180.00", 100_000))  # not a watched symbol
    rows = recorder.take()
    assert [(row.symbol, row.size) for row in rows] == [("SPY", 1300)]
    assert rows[0].exchange == 57 and rows[0].condition == 0
    assert recorder.take() == []


def test_the_exchange_timestamp_is_used_when_there_is_one_else_the_receipt_time() -> None:
    recorder = LargePrintRecorder()
    exchange_time = datetime(2026, 10, 16, 13, 59, 58, tzinfo=UTC)
    recorder.observe(_event("QQQ", "600.00", 2000), exchange_time)
    recorder.observe(_event("QQQ", "600.00", 2000))
    first, second = recorder.take()
    assert first.time == exchange_time and second.time == NOW and first.received_at == NOW


def test_at_market_is_judged_against_the_last_price_that_may_set_the_displayed_price() -> None:
    recorder = LargePrintRecorder()
    recorder.observe(_event("SPY", "770.00", 10))  # a normal print: the reference
    recorder.observe(_event("SPY", "770.40", 2000))  # within 0.1%: at market
    recorder.observe(_event("SPY", "778.00", 2000, condition=3))  # ~1% away, average price: not at market
    recorder.observe(_event("SPY", "770.30", 2000, condition=124))  # contingent: kept, never the reference
    assert [row.at_market for row in recorder.take()] == [True, False, True]


def test_a_large_print_away_from_the_market_does_not_become_the_reference() -> None:
    recorder = LargePrintRecorder()
    recorder.observe(_event("SPY", "770.00", 10))
    recorder.observe(_event("SPY", "778.00", 2000, condition=0))  # far, condition 0: kept, but not the new reference
    recorder.observe(_event("SPY", "770.20", 2000))  # still judged against 770.00
    assert [row.at_market for row in recorder.take()] == [False, True]


def test_at_market_is_unknown_until_a_reference_price_exists() -> None:
    recorder = LargePrintRecorder()
    recorder.observe(_event("SPY", "770.00", 2000))
    assert recorder.take()[0].at_market is None


def test_a_failed_flush_puts_its_rows_back_ahead_of_newer_ones_and_memory_stays_bounded() -> None:
    recorder = LargePrintRecorder()
    recorder.observe(_event("SPY", "770.00", 2000))
    rows = recorder.take()
    recorder.observe(_event("SPY", "771.00", 2000))
    recorder.put_back(rows)
    assert [str(row.price) for row in recorder.take()] == ["770.00", "771.00"]
    for _ in range(MAX_PENDING_ROWS + 50):
        recorder.observe(_event("SPY", "770.00", 2000))
    assert len(recorder.take()) == MAX_PENDING_ROWS


def test_the_parser_carries_the_exchange_only_when_it_is_a_plain_integer() -> None:
    symbols = {"SPY": UnderlyingKind.EQUITY}
    contract = {"security_type": "STOCK", "root": "SPY"}
    parsed = parse_underlying_trade_message({"contract": contract, "trade": {"size": 1500, "price": 770.5, "exchange": 57}}, symbols)
    assert parsed is not None and parsed.event.exchange == 57
    for exchange in (None, True, "57"):
        trade = {"size": 1500, "price": 770.5, "exchange": exchange}
        parsed = parse_underlying_trade_message({"contract": contract, "trade": trade}, symbols)
        assert parsed is not None and parsed.event.exchange is None
    parsed = parse_underlying_trade_message({"contract": contract, "trade": {"size": 1500, "price": 770.5}}, symbols)
    assert parsed is not None and parsed.event.exchange is None
