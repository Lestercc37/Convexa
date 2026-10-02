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
                "contract": {"security_type": "STOCK", "root": "AAPL"},
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
