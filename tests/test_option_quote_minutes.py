from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend import stream_processor_worker as worker
from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.sync_read_adapter import SyncStorageAsyncReadAdapter
from backend.domain.entities import OptionQuoteMinute, QuoteEvent
from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.stream_processor_worker import _handle_raw_frame, _ProcessorState

MINUTE = datetime(2026, 10, 14, 10, 5, tzinfo=EASTERN_TIME)
OCC = "SPXW261014C07850000"


class _FakeRelay:
    def publish_quote(self, event) -> None:
        pass

    def publish_trade(self, event) -> None:
        pass


class _FakePrice:
    async def persist_if_due(self, event) -> None:
        pass


def _state() -> _ProcessorState:
    return _ProcessorState(_FakeRelay(), _FakePrice())  # type: ignore[arg-type]


def _quote(occ: str, bid: str, ask: str, second: int = 0) -> QuoteEvent:
    return QuoteEvent(
        symbol="SPX",
        occ_symbol=occ,
        as_of=datetime(2026, 10, 14, 14, 5, second, tzinfo=UTC),
        bid=Decimal(bid),
        ask=Decimal(ask),
    )


def test_the_last_quote_of_the_minute_is_the_row() -> None:
    state = _state()
    state.record_minute_quote(_quote(OCC, "10.00", "10.50", 5))
    state.record_minute_quote(_quote(OCC, "10.10", "10.60", 40))

    rows = state.take_minute_rows(MINUTE)

    assert rows == [OptionQuoteMinute(MINUTE, OCC, Decimal("10.10"), Decimal("10.60"), _quote(OCC, "0", "0", 40).as_of)]


def test_taking_a_minute_starts_a_new_one() -> None:
    state = _state()
    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))
    assert state.take_minute_rows(MINUTE)
    assert state.take_minute_rows(MINUTE) == []


def test_a_contract_whose_quote_did_not_change_has_no_new_row() -> None:
    state = _state()
    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))
    first = state.take_minute_rows(MINUTE)
    state.mark_minute_rows_written(first)

    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))  # same bid/ask, only the time moved
    assert state.take_minute_rows(MINUTE.replace(minute=6)) == []

    state.record_minute_quote(_quote(OCC, "10.00", "10.60"))
    again = state.take_minute_rows(MINUTE.replace(minute=7))
    assert [(row.occ_symbol, row.ask) for row in again] == [(OCC, Decimal("10.60"))]


def test_rows_not_marked_as_written_are_offered_again_when_the_quote_comes_back() -> None:
    state = _state()
    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))
    assert state.take_minute_rows(MINUTE)  # the write failed: nothing was marked
    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))
    assert state.take_minute_rows(MINUTE.replace(minute=6))


def test_the_written_memory_restarts_on_a_new_session_day() -> None:
    state = _state()
    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))
    state.mark_minute_rows_written(state.take_minute_rows(MINUTE))

    next_day = MINUTE.replace(day=15)
    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))
    assert len(state.take_minute_rows(next_day)) == 1  # same quote as yesterday's last, but a new day


def test_the_recorder_can_be_switched_off() -> None:
    state = _state()
    state.minute_quotes_enabled = False
    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))
    assert state.take_minute_rows(MINUTE) == []


def test_a_raw_quote_frame_reaches_the_recorder_but_a_repeat_does_not() -> None:
    state = _state()
    frame = json.dumps(
        {
            "header": {"type": "QUOTE", "status": "CONNECTED"},
            "contract": {"security_type": "OPTION", "root": "SPXW", "expiration": 20261014, "strike": 7850000, "right": "C"},
            "quote": {"bid": 10.0, "ask": 10.5, "date": 20261014, "ms_of_day": 36300000},
        }
    )

    _handle_raw_frame(state, frame)
    assert len(state.minute_quotes) == 1
    state.minute_quotes.clear()

    _handle_raw_frame(state, frame)  # identical bid/ask within the refresh window: dropped before parsing
    assert state.minute_quotes == {}


@pytest.mark.asyncio
async def test_the_storage_adapter_keeps_the_rows_in_memory() -> None:
    adapter = SyncStorageAsyncReadAdapter(InMemoryStorage())
    row = OptionQuoteMinute(MINUTE, OCC, Decimal(1), Decimal(2), MINUTE)
    await adapter.save_option_quote_minutes([row])
    assert adapter.option_quote_minutes == [row]


@pytest.mark.asyncio
async def test_the_periodic_task_writes_the_minute_and_marks_it(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))
    storage = AsyncMock()
    container = type("C", (), {"async_market_storage": storage})()
    monkeypatch.setattr(worker, "is_market_open", lambda _minute: True)
    monkeypatch.setattr(worker, "MINUTE_QUOTES_FLUSH_DELAY_SECONDS", -60.0)  # fire the first flush immediately

    task = asyncio.create_task(worker._record_option_quote_minutes_periodically(container, state))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if storage.save_option_quote_minutes.await_count:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    storage.save_option_quote_minutes.assert_awaited()
    written = storage.save_option_quote_minutes.await_args.args[0]
    assert [row.occ_symbol for row in written] == [OCC]
    assert state._minute_quotes_last_written[OCC] == (Decimal("10.00"), Decimal("10.50"))


@pytest.mark.asyncio
async def test_outside_the_session_nothing_is_written_and_a_failed_write_does_not_kill_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))
    storage = AsyncMock()
    storage.save_option_quote_minutes.side_effect = RuntimeError("database unavailable")
    container = type("C", (), {"async_market_storage": storage})()
    monkeypatch.setattr(worker, "MINUTE_QUOTES_FLUSH_DELAY_SECONDS", -60.0)

    monkeypatch.setattr(worker, "is_market_open", lambda _minute: False)
    task = asyncio.create_task(worker._record_option_quote_minutes_periodically(container, state))
    await asyncio.sleep(0.8)  # the loop never sleeps less than 0.5 s, so the first flush has happened
    assert not task.done()
    storage.save_option_quote_minutes.assert_not_awaited()
    assert state.minute_quotes == {}  # the minute was handed over and dropped

    monkeypatch.setattr(worker, "is_market_open", lambda _minute: True)
    state.record_minute_quote(_quote(OCC, "10.00", "10.50"))
    for _ in range(150):
        await asyncio.sleep(0.02)
        if storage.save_option_quote_minutes.await_count:
            break
    assert storage.save_option_quote_minutes.await_count >= 1
    assert not task.done()  # the exception was logged, not raised
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
