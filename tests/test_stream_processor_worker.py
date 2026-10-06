from __future__ import annotations

import asyncio
import inspect
import json
from decimal import Decimal

import pytest

from backend.domain.entities import FlowEvent, QuoteEvent, UnderlyingTradeEvent
from backend.stream_processor_worker import _handle_raw_frame, _ProcessorState


class _FakeWhaleAlertsRelay:
    def __init__(self) -> None:
        self.published_quotes: list[QuoteEvent] = []
        self.published_trades: list[FlowEvent] = []

    def publish_quote(self, event: QuoteEvent) -> None:
        self.published_quotes.append(event)

    def publish_trade(self, event: FlowEvent) -> None:
        self.published_trades.append(event)


class _FakePriceUseCase:
    """Tracks calls and lets a test control exactly when persist_if_due
    actually resolves -- see TestNoBlockingWaitOnUnderlyingTrades below,
    which uses this to prove _handle_raw_frame never awaits it."""

    def __init__(self) -> None:
        self.calls: list[UnderlyingTradeEvent] = []
        self._gate: asyncio.Event = asyncio.Event()
        self._gate.set()  # resolves immediately by default

    def block_until_released(self) -> None:
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    async def persist_if_due(self, event: UnderlyingTradeEvent) -> None:
        self.calls.append(event)
        await self._gate.wait()


def _state() -> tuple[_ProcessorState, _FakeWhaleAlertsRelay, _FakePriceUseCase]:
    relay = _FakeWhaleAlertsRelay()
    price_use_case = _FakePriceUseCase()
    state = _ProcessorState(relay, price_use_case)  # type: ignore[arg-type]
    return state, relay, price_use_case


class TestRoutesQuoteMessages:
    def test_a_real_shaped_quote_is_published_to_whale_alerts_relay(self) -> None:
        state, relay, _price = _state()
        raw = json.dumps(
            {
                "header": {"type": "QUOTE", "status": "CONNECTED"},
                "contract": {
                    "security_type": "OPTION",
                    "root": "SPY",
                    "expiration": 20260918,
                    "strike": 770000,
                    "right": "C",
                },
                "quote": {"bid": 1.08, "ask": 1.09},
            }
        )

        _handle_raw_frame(state, raw)

        assert len(relay.published_quotes) == 1
        assert relay.published_quotes[0].bid == Decimal("1.08")


class TestRoutesOptionTrades:
    def test_a_trade_with_a_price_updates_volume_and_publishes(self) -> None:
        state, relay, _price = _state()
        raw = json.dumps(
            {
                "header": {"type": "TRADE", "status": "CONNECTED"},
                "contract": {
                    "security_type": "OPTION",
                    "root": "SPY",
                    "expiration": 20260918,
                    "strike": 770000,
                    "right": "C",
                },
                "trade": {"size": 10, "price": 1.09},
            }
        )

        _handle_raw_frame(state, raw)

        assert state.cumulative_volume["SPY260918C00770000"] == 10
        assert len(relay.published_trades) == 1

    def test_a_trade_with_no_price_updates_volume_but_does_not_publish(self) -> None:
        state, relay, _price = _state()
        raw = json.dumps(
            {
                "header": {"type": "TRADE", "status": "CONNECTED"},
                "contract": {
                    "security_type": "OPTION",
                    "root": "SPY",
                    "expiration": 20260918,
                    "strike": 770000,
                    "right": "C",
                },
                "trade": {"size": 10},
            }
        )

        _handle_raw_frame(state, raw)

        assert state.cumulative_volume["SPY260918C00770000"] == 10
        assert relay.published_trades == []

    def test_volume_accumulates_across_multiple_trades_for_the_same_contract(self) -> None:
        state, _relay, _price = _state()
        raw = json.dumps(
            {
                "header": {"type": "TRADE", "status": "CONNECTED"},
                "contract": {
                    "security_type": "OPTION",
                    "root": "SPY",
                    "expiration": 20260918,
                    "strike": 770000,
                    "right": "C",
                },
                "trade": {"size": 10, "price": 1.09},
            }
        )

        _handle_raw_frame(state, raw)
        _handle_raw_frame(state, raw)
        _handle_raw_frame(state, raw)

        assert state.cumulative_volume["SPY260918C00770000"] == 30


class TestNoBlockingWaitOnUnderlyingTrades:
    """2026-10-02, v2: the user's explicit requirement -- confirm there
    is no point of waiting/response between the raw-frame handler and
    anything it schedules, a real test, not a disguised variant of the
    pattern that already failed (v1, PR #226). See docs/stream-processor-
    split-postmortem-2026-10-02.md."""

    def test_handle_raw_frame_is_not_a_coroutine_function(self) -> None:
        # Structural guarantee: if this were ever changed to `async def`
        # and awaited persist_if_due inline, that change alone would
        # flip this assertion -- a reviewer (or this test) catches it
        # before it ships, regardless of how the call site looks.
        assert not inspect.iscoroutinefunction(_handle_raw_frame)

    @pytest.mark.asyncio
    async def test_a_slow_persist_if_due_does_not_block_handling_the_next_frame(self) -> None:
        state, _relay, price = _state()
        price.block_until_released()  # persist_if_due's own await never resolves yet
        underlying_raw = json.dumps(
            {
                "header": {"type": "TRADE", "status": "CONNECTED"},
                "contract": {"security_type": "STOCK", "root": "NVDA"},
                "trade": {"size": 500, "price": 184.51},
            }
        )
        quote_raw = json.dumps(
            {
                "header": {"type": "QUOTE", "status": "CONNECTED"},
                "contract": {
                    "security_type": "OPTION",
                    "root": "SPY",
                    "expiration": 20260918,
                    "strike": 770000,
                    "right": "C",
                },
                "quote": {"bid": 1.08, "ask": 1.09},
            }
        )

        # If _handle_raw_frame awaited persist_if_due directly, this call
        # itself would hang forever (price.block_until_released() above
        # never resolves) and the test would time out. It doesn't --
        # proving the underlying-trade frame's handling returns
        # immediately regardless of how slow persisting it turns out to
        # be.
        _handle_raw_frame(state, underlying_raw)
        _handle_raw_frame(state, quote_raw)  # a later frame is handled too, unblocked

        assert state  # reached this line at all is the real assertion

        # Let the scheduled task actually start running (create_task
        # doesn't run synchronously) and confirm persist_if_due really
        # was called, just not waited on by _handle_raw_frame itself.
        await asyncio.sleep(0.01)
        assert len(price.calls) == 1
        price.release()
        await asyncio.sleep(0.01)  # let the now-unblocked task finish cleanly


class TestRepeatedQuotesAreNotRelayed:
    """2026-10-05 open: parsing and relaying every size-only quote update kept
    the processor at 99% CPU and its relay queue overflowing."""

    @staticmethod
    def _quote(bid: float, ask: float, strike: int = 770000) -> str:
        return json.dumps(
            {
                "header": {"type": "QUOTE", "status": "CONNECTED"},
                "contract": {
                    "security_type": "OPTION",
                    "root": "SPY",
                    "expiration": 20260918,
                    "strike": strike,
                    "right": "C",
                },
                "quote": {"bid": bid, "ask": ask},
            }
        )

    def test_the_same_bid_ask_within_the_refresh_window_is_forwarded_once(self) -> None:
        state, relay, _price = _state()
        for _ in range(50):
            _handle_raw_frame(state, self._quote(1.08, 1.09))
        assert len(relay.published_quotes) == 1

    def test_a_changed_price_is_forwarded_immediately(self) -> None:
        state, relay, _price = _state()
        _handle_raw_frame(state, self._quote(1.08, 1.09))
        _handle_raw_frame(state, self._quote(1.09, 1.10))
        assert [str(q.bid) for q in relay.published_quotes] == ["1.08", "1.09"]

    def test_contracts_are_tracked_independently(self) -> None:
        state, relay, _price = _state()
        _handle_raw_frame(state, self._quote(1.08, 1.09, strike=770000))
        _handle_raw_frame(state, self._quote(1.08, 1.09, strike=771000))
        assert len(relay.published_quotes) == 2

    def test_an_unchanged_quote_is_refreshed_after_the_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import backend.stream_processor_worker as module

        clock = {"now": 1000.0}
        monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
        state, relay, _price = _state()
        _handle_raw_frame(state, self._quote(1.08, 1.09))
        clock["now"] += module.QUOTE_REFRESH_SECONDS + 0.01
        _handle_raw_frame(state, self._quote(1.08, 1.09))
        assert len(relay.published_quotes) == 2


class TestResumeCumulativeVolume:
    """2026-10-05: a processor restart mid-session reset the day's volume to
    zero (its first export overwrote the stored rows)."""

    class _Container:
        def __init__(self, storage) -> None:
            self.storage = storage

    @pytest.mark.asyncio
    async def test_todays_stored_volume_seeds_the_counters(self) -> None:
        from backend.adapters.storage.memory import InMemoryStorage
        from backend.stream_processor_worker import _resume_cumulative_volume

        storage = InMemoryStorage()
        storage.save_cumulative_volumes({"SPY261005C00770000": 700})
        state, _relay, _price = _state()

        await _resume_cumulative_volume(self._Container(storage), state)

        assert state.cumulative_volume == {"SPY261005C00770000": 700}
        raw = json.dumps(
            {
                "header": {"type": "TRADE", "status": "CONNECTED"},
                "contract": {
                    "security_type": "OPTION",
                    "root": "SPY",
                    "expiration": 20261005,
                    "strike": 770000,
                    "right": "C",
                },
                "trade": {"size": 10, "price": 1.09},
            }
        )
        _handle_raw_frame(state, raw)
        assert state.cumulative_volume["SPY261005C00770000"] == 710, "new trades add to the resumed total"

    @pytest.mark.asyncio
    async def test_a_storage_failure_starts_from_zero_instead_of_blocking_startup(self) -> None:
        from backend.stream_processor_worker import _resume_cumulative_volume

        class _Broken:
            def get_cumulative_volumes_since(self, _since) -> dict:
                raise RuntimeError("db down")

        state, _relay, _price = _state()
        await _resume_cumulative_volume(self._Container(_Broken()), state)
        assert state.cumulative_volume == {}


class TestFrameMix:
    def test_frames_are_counted_per_type_and_root_including_repeats(self) -> None:
        state, _relay, _price = _state()
        quote = TestRepeatedQuotesAreNotRelayed._quote(1.08, 1.09)
        for _ in range(3):
            _handle_raw_frame(state, quote)
        assert state.frame_mix[("QUOTE", "SPY")] == 3
        assert state.repeated_quotes == 2


class TestPriceWritesAreCoalescedPerSymbol:
    """2026-10-05 open: one task per tick for the tick-level symbols exhausted
    the Postgres pool (QueuePool 5+10) within a minute, the processor fell
    behind and the stored SPX price froze."""

    @staticmethod
    def _tick(symbol: str, price: float) -> str:
        return json.dumps(
            {
                "header": {"type": "TRADE", "status": "CONNECTED"},
                "contract": {"security_type": "INDEX" if symbol in ("SPX", "NDX", "VIX") else "STOCK", "root": symbol},
                "trade": {"size": 1, "price": price},
            }
        )

    @pytest.mark.asyncio
    async def test_a_burst_behind_a_slow_write_keeps_one_write_in_flight_and_ends_on_the_newest_tick(self) -> None:
        state, _relay, price = _state()
        price.block_until_released()

        _handle_raw_frame(state, self._tick("SPX", 7700.0))
        await asyncio.sleep(0.01)  # the first write starts and blocks
        for i in range(1, 500):
            _handle_raw_frame(state, self._tick("SPX", 7700.0 + i))
        await asyncio.sleep(0.01)
        assert len(price.calls) == 1, "only one write may be in flight for a symbol"

        price.release()
        await asyncio.sleep(0.05)
        assert len(price.calls) == 2, "the 499 ticks in between collapse into the newest one"
        assert float(price.calls[-1].price) == 7700.0 + 499

    @pytest.mark.asyncio
    async def test_symbols_are_written_independently_and_concurrency_is_capped(self) -> None:
        state, _relay, price = _state()
        price.block_until_released()
        symbols = ["SPX", "SPY", "QQQ", "IWM", "NVDA", "NDX", "VIX"]
        for symbol in symbols:
            _handle_raw_frame(state, self._tick(symbol, 100.0))
        await asyncio.sleep(0.01)
        assert len(price.calls) == 4, "PRICE_WRITE_CONCURRENCY writes at a time across all symbols"

        price.release()
        await asyncio.sleep(0.05)
        assert {c.symbol for c in price.calls} == set(symbols)

    @pytest.mark.asyncio
    async def test_a_failing_write_does_not_stop_later_ticks_for_that_symbol(self) -> None:
        state, _relay, price = _state()
        original = price.persist_if_due
        attempts = {"n": 0}

        async def flaky(event: UnderlyingTradeEvent) -> None:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("pool timeout")
            await original(event)

        price.persist_if_due = flaky  # type: ignore[method-assign]
        _handle_raw_frame(state, self._tick("SPX", 7700.0))
        await asyncio.sleep(0.02)
        _handle_raw_frame(state, self._tick("SPX", 7701.0))
        await asyncio.sleep(0.02)
        assert [float(c.price) for c in price.calls] == [7701.0]


class TestMalformedFrames:
    def test_invalid_json_does_not_raise(self) -> None:
        state, relay, _price = _state()
        _handle_raw_frame(state, "not json at all")  # must not raise
        assert relay.published_quotes == []
        assert relay.published_trades == []

    def test_an_unknown_message_type_is_ignored(self) -> None:
        state, relay, _price = _state()
        raw = json.dumps({"header": {"type": "REQ_RESPONSE"}})
        _handle_raw_frame(state, raw)
        assert relay.published_quotes == []
        assert relay.published_trades == []


class TestVolumeDayRollover:
    """2026-10-06: nothing reset the cumulative volume between days, so a
    contract trading on several days kept adding to the previous totals."""

    def test_the_counters_are_cleared_when_the_et_date_changes(self) -> None:
        from datetime import date

        state, _relay, _price = _state()
        assert state.roll_volume_day(date(2026, 10, 6)) is False  # first call only records the day
        state.cumulative_volume["SPY261007C00780000"] = 500
        assert state.roll_volume_day(date(2026, 10, 6)) is False
        assert state.cumulative_volume == {"SPY261007C00780000": 500}

        assert state.roll_volume_day(date(2026, 10, 7)) is True
        assert state.cumulative_volume == {}

    @pytest.mark.asyncio
    async def test_resume_records_todays_date_so_the_first_export_does_not_wipe_the_resumed_volume(self) -> None:
        from datetime import datetime

        from backend.adapters.storage.memory import InMemoryStorage
        from backend.domain.use_cases.market_hours import EASTERN_TIME
        from backend.stream_processor_worker import _resume_cumulative_volume

        class _Container:
            storage = InMemoryStorage()

        _Container.storage.save_cumulative_volumes({"SPY261007C00780000": 500})
        state, _relay, _price = _state()
        await _resume_cumulative_volume(_Container(), state)

        assert state.volume_date == datetime.now(EASTERN_TIME).date()
        assert state.roll_volume_day(datetime.now(EASTERN_TIME).date()) is False
        assert state.cumulative_volume == {"SPY261007C00780000": 500}
