from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.domain.entities import FlowEvent, FlowEventType, LatestQuote, QuoteEvent, Side
from backend.domain.use_cases.stream_whale_alerts import StreamWhaleAlertsUseCase

NOW = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
OCC_SYMBOL = "SPY260918C00770000"


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[FlowEvent, LatestQuote | None]] = []
        # One entry per process_trade_batch() call, each holding that
        # call's own events in order -- lets tests tell a single grouped
        # call apart from several individual ones, which `.calls` alone
        # (identical either way) can't distinguish.
        self.batch_calls: list[list[tuple[FlowEvent, LatestQuote | None]]] = []

    def process_trade(self, event: FlowEvent, quote: LatestQuote | None) -> tuple[()]:
        self.calls.append((event, quote))
        return ()

    def process_trade_batch(
        self, events: list[tuple[FlowEvent, LatestQuote | None]]
    ) -> tuple[()]:
        self.batch_calls.append(list(events))
        for event, quote in events:
            self.process_trade(event, quote)
        return ()


class _FakeProvider:
    """A finite, deterministic stand-in for IDataProvider's two streams —
    each just replays a fixed list, so tests can drive quote/trade
    ordering explicitly instead of racing real concurrent scheduling."""

    def __init__(
        self, quotes: list[QuoteEvent] | None = None, trades: list[FlowEvent] | None = None
    ) -> None:
        self._quotes = quotes or []
        self._trades = trades or []

    async def stream_quotes(self, underlying: str) -> AsyncIterator[QuoteEvent]:
        for quote in self._quotes:
            yield quote

    async def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]:
        for trade in self._trades:
            yield trade


def _quote(bid: str, ask: str, occ_symbol: str = OCC_SYMBOL) -> QuoteEvent:
    return QuoteEvent(symbol="SPY", occ_symbol=occ_symbol, as_of=NOW, bid=Decimal(bid), ask=Decimal(ask))


def _trade(premium: str, occ_symbol: str = OCC_SYMBOL) -> FlowEvent:
    return FlowEvent(
        symbol="SPY",
        occ_symbol=occ_symbol,
        as_of=NOW,
        event_type=FlowEventType.UNUSUAL,
        premium=Decimal(premium),
        size=1,
        aggressor_side=Side.UNKNOWN,
    )


@pytest.mark.asyncio
async def test_a_trade_is_classified_against_the_quote_that_already_arrived_for_it() -> None:
    engine = _FakeEngine()
    trade = _trade("100")
    provider = _FakeProvider(quotes=[_quote("1.08", "1.09")], trades=[trade])
    use_case = StreamWhaleAlertsUseCase(provider, engine)

    # Deterministic ordering: fully drain the quote stream before the
    # trade stream, instead of racing asyncio.gather()'s real concurrent
    # scheduling — proves the tracking mechanism itself, independent of
    # timing.
    await use_case._consume_quotes("SPY")
    await use_case._consume_trades("SPY")

    assert len(engine.calls) == 1
    event, quote = engine.calls[0]
    assert event is trade
    assert quote == LatestQuote(bid=Decimal("1.08"), ask=Decimal("1.09"), as_of=NOW)


@pytest.mark.asyncio
async def test_a_trade_with_no_quote_seen_yet_is_passed_none() -> None:
    engine = _FakeEngine()
    trade = _trade("100")
    provider = _FakeProvider(trades=[trade])
    use_case = StreamWhaleAlertsUseCase(provider, engine)

    await use_case._consume_trades("SPY")

    assert len(engine.calls) == 1
    _, quote = engine.calls[0]
    assert quote is None


@pytest.mark.asyncio
async def test_a_newer_quote_for_the_same_contract_replaces_the_older_one() -> None:
    engine = _FakeEngine()
    trade = _trade("100")
    provider = _FakeProvider(
        quotes=[_quote("1.08", "1.09"), _quote("1.10", "1.11")],
        trades=[trade],
    )
    use_case = StreamWhaleAlertsUseCase(provider, engine)

    await use_case._consume_quotes("SPY")
    await use_case._consume_trades("SPY")

    _, quote = engine.calls[0]
    assert quote == LatestQuote(bid=Decimal("1.10"), ask=Decimal("1.11"), as_of=NOW)


@pytest.mark.asyncio
async def test_a_quote_for_a_different_contract_does_not_affect_this_ones_trade() -> None:
    engine = _FakeEngine()
    trade = _trade("100", occ_symbol=OCC_SYMBOL)
    provider = _FakeProvider(
        quotes=[_quote("1.08", "1.09", occ_symbol="SPY260918C00780000")],
        trades=[trade],
    )
    use_case = StreamWhaleAlertsUseCase(provider, engine)

    await use_case._consume_quotes("SPY")
    await use_case._consume_trades("SPY")

    _, quote = engine.calls[0]
    assert quote is None


@pytest.mark.asyncio
async def test_run_consumes_both_streams_concurrently_and_returns_once_both_are_exhausted() -> None:
    engine = _FakeEngine()
    provider = _FakeProvider(quotes=[_quote("1.08", "1.09")], trades=[_trade("100")])
    use_case = StreamWhaleAlertsUseCase(provider, engine)

    # A finite provider means run() must actually complete — a real,
    # infinite provider would never return (callers cancel the task
    # instead, see core/whale_alerts_stream.py).
    await asyncio.wait_for(use_case.run("SPY"), timeout=1)

    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_consume_trades_runs_process_trade_on_a_worker_thread_not_the_event_loop() -> None:
    """The actual fix (2026-09-10): process_trade() can do a blocking
    synchronous DB write (WhaleAlertsEngine._emit -> IStorage.
    save_whale_alert) -- confirmed live with a py-spy dump of a frozen
    worker process, real market hours: calling it directly from this
    coroutine blocked ThetaStreamHub's own read loop too, since they
    share one event loop. This proves process_trade() now actually runs
    on a different OS thread, not just that the test suite still
    passes."""
    main_thread_id = threading.get_ident()
    seen_thread_ids: list[int] = []

    class _ThreadRecordingEngine:
        def process_trade(self, event: FlowEvent, quote: LatestQuote | None) -> tuple[()]:
            seen_thread_ids.append(threading.get_ident())
            return ()

        def process_trade_batch(
            self, events: list[tuple[FlowEvent, LatestQuote | None]]
        ) -> tuple[()]:
            for event, quote in events:
                self.process_trade(event, quote)
            return ()

    provider = _FakeProvider(trades=[_trade("100")])
    use_case = StreamWhaleAlertsUseCase(provider, _ThreadRecordingEngine())

    await use_case._consume_trades("SPY")

    assert len(seen_thread_ids) == 1
    assert seen_thread_ids[0] != main_thread_id


@pytest.mark.asyncio
async def test_consume_trades_uses_the_passed_executor_not_the_default_shared_one() -> None:
    """The actual fix (2026-09-10, same day as the above): offloading
    alone still shared asyncio.to_thread()'s default executor with the
    REST scheduler and reconcile() -- confirmed live, real market open,
    that the scheduler running concurrently was enough to make a busy
    symbol's own trade queue fill up and start dropping messages. A
    dedicated executor (owned by WhaleAlertsStreamManager) must actually
    be the one process_trade() runs on, not just present and unused."""
    seen_thread_names: list[str] = []

    class _ThreadNameRecordingEngine:
        def process_trade(self, event: FlowEvent, quote: LatestQuote | None) -> tuple[()]:
            seen_thread_names.append(threading.current_thread().name)
            return ()

        def process_trade_batch(
            self, events: list[tuple[FlowEvent, LatestQuote | None]]
        ) -> tuple[()]:
            for event, quote in events:
                self.process_trade(event, quote)
            return ()

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-whale-alerts")
    try:
        provider = _FakeProvider(trades=[_trade("100")])
        use_case = StreamWhaleAlertsUseCase(
            provider, _ThreadNameRecordingEngine(), executor=executor
        )

        await use_case._consume_trades("SPY")

        assert len(seen_thread_names) == 1
        assert seen_thread_names[0].startswith("test-whale-alerts")
    finally:
        executor.shutdown(wait=False)


class _SlowTrickleProvider:
    """Yields each of `trades` with a real `asyncio.sleep(gap_seconds)`
    beforehand -- unlike _FakeProvider (which yields everything
    instantly), this can straddle a real batch window boundary, needed to
    prove a batch flushes on time and doesn't wait forever for a full one."""

    def __init__(self, trades: list[FlowEvent], gap_seconds: float) -> None:
        self._trades = trades
        self._gap_seconds = gap_seconds

    async def stream_quotes(self, underlying: str) -> AsyncIterator[QuoteEvent]:
        if False:
            yield

    async def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]:
        for trade in self._trades:
            await asyncio.sleep(self._gap_seconds)
            yield trade


class TestSelectiveBatchingForHighVolumeSymbols:
    """Fix (2026-09-10): confirmed live, via symbol-tagged queue
    instrumentation, that SPY -- not SPX, the prior suspect -- was the
    sole source of every CRITICAL trade-queue drop across a full trading
    day (2,568 of them, zero on any other symbol). SPY's own
    one-executor-call-per-trade path couldn't keep up with its real tick
    rate even on an uncontended dedicated thread; HIGH_VOLUME_BATCHED_SYMBOLS
    routes only SPY through a micro-batched path instead, leaving every
    other symbol (never observed to have this problem) on the original,
    lower-latency single-message path."""

    @pytest.mark.asyncio
    async def test_a_batched_symbol_routes_through_process_trade_batch(self) -> None:
        engine = _FakeEngine()
        provider = _FakeProvider(trades=[_trade("100")])
        use_case = StreamWhaleAlertsUseCase(provider, engine)

        await use_case._consume_trades("SPY")

        assert len(engine.batch_calls) == 1
        assert len(engine.calls) == 1

    @pytest.mark.asyncio
    async def test_a_non_batched_symbol_still_uses_the_single_message_path(self) -> None:
        engine = _FakeEngine()
        provider = _FakeProvider(trades=[_trade("100")])
        use_case = StreamWhaleAlertsUseCase(provider, engine)

        await use_case._consume_trades("AAPL")

        assert engine.batch_calls == []
        assert len(engine.calls) == 1

    @pytest.mark.asyncio
    async def test_rapidly_arriving_trades_are_grouped_into_one_batch_call(self) -> None:
        """All three trades are available on the stream instantly (no
        real delay between them) -- well within BATCH_WINDOW_SECONDS --
        so they must land in a single process_trade_batch() call, not
        three separate ones, preserving their original order."""
        engine = _FakeEngine()
        trades = [_trade("100"), _trade("200"), _trade("300")]
        provider = _FakeProvider(trades=trades)
        use_case = StreamWhaleAlertsUseCase(provider, engine)

        await use_case._consume_trades("SPY")

        assert len(engine.batch_calls) == 1
        assert [event for event, _ in engine.batch_calls[0]] == trades

    @pytest.mark.asyncio
    async def test_a_slow_trickle_still_flushes_within_the_batch_window(self) -> None:
        """Two trades, spaced further apart than BATCH_WINDOW_SECONDS --
        each must flush on its own (the batch window elapsing, not a full
        batch) rather than the first trade waiting indefinitely for a
        second one that's already arrived by the time it matters."""
        from backend.domain.use_cases.stream_whale_alerts import BATCH_WINDOW_SECONDS

        engine = _FakeEngine()
        trades = [_trade("100"), _trade("200")]
        provider = _SlowTrickleProvider(trades, gap_seconds=BATCH_WINDOW_SECONDS * 3)
        use_case = StreamWhaleAlertsUseCase(provider, engine)

        await asyncio.wait_for(use_case._consume_trades("SPY"), timeout=5)

        assert len(engine.batch_calls) == 2
        assert [event for event, _ in engine.batch_calls[0]] == [trades[0]]
        assert [event for event, _ in engine.batch_calls[1]] == [trades[1]]


class TestBatchCollector:
    """Direct unit tests for _BatchCollector -- the primitive
    _consume_trades_batched relies on to turn a bare AsyncIterator into
    time-boxed batches without ever cancelling a pending __anext__() call
    (see its own docstring for why that would silently and permanently
    end the underlying stream)."""

    @staticmethod
    async def _instant_stream(values: list[int]) -> AsyncIterator[int]:
        for value in values:
            yield value

    @staticmethod
    async def _spaced_stream(values: list[int], gap_seconds: float) -> AsyncIterator[int]:
        for value in values:
            await asyncio.sleep(gap_seconds)
            yield value

    @pytest.mark.asyncio
    async def test_returns_none_for_an_already_exhausted_stream(self) -> None:
        from backend.domain.use_cases.stream_whale_alerts import _BatchCollector

        collector = _BatchCollector(self._instant_stream([]).__aiter__())

        result = await collector.collect(window_seconds=1, max_size=10)

        assert result is None

    @pytest.mark.asyncio
    async def test_collects_everything_available_instantly_in_one_batch(self) -> None:
        from backend.domain.use_cases.stream_whale_alerts import _BatchCollector

        collector = _BatchCollector(self._instant_stream([1, 2, 3]).__aiter__())

        result = await collector.collect(window_seconds=1, max_size=10)

        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_stops_at_max_size_even_if_more_is_available(self) -> None:
        from backend.domain.use_cases.stream_whale_alerts import _BatchCollector

        collector = _BatchCollector(self._instant_stream([1, 2, 3, 4, 5]).__aiter__())

        result = await collector.collect(window_seconds=1, max_size=2)

        assert result == [1, 2]

    @pytest.mark.asyncio
    async def test_flushes_on_the_window_elapsing_with_a_partial_batch(self) -> None:
        from backend.domain.use_cases.stream_whale_alerts import _BatchCollector

        collector = _BatchCollector(self._spaced_stream([1, 2], gap_seconds=0.2).__aiter__())

        result = await asyncio.wait_for(
            collector.collect(window_seconds=0.05, max_size=10), timeout=2
        )

        # The first value starts the batch; the second arrives after the
        # (much shorter) window has already elapsed, so it must NOT be
        # waited for.
        assert result == [1]
        await collector.aclose()

    @pytest.mark.asyncio
    async def test_a_value_pending_past_the_window_is_not_lost_on_the_next_collect_call(
        self,
    ) -> None:
        """The actual bug this class exists to fix: a naive
        `asyncio.wait_for(stream.__anext__(), timeout=...)` cancels the
        pending call on timeout, which (for a `while True: yield await
        queue.get()`-shaped generator, exactly what stream_trades() is)
        permanently closes the generator -- the second value would never
        arrive, in this call or any later one. Two separate collect()
        calls, each returning its own single-item batch, is the proof
        nothing was lost."""
        from backend.domain.use_cases.stream_whale_alerts import _BatchCollector

        collector = _BatchCollector(self._spaced_stream([1, 2], gap_seconds=0.2).__aiter__())

        first_batch = await asyncio.wait_for(
            collector.collect(window_seconds=0.05, max_size=10), timeout=2
        )
        second_batch = await asyncio.wait_for(
            collector.collect(window_seconds=0.05, max_size=10), timeout=2
        )

        assert first_batch == [1]
        assert second_batch == [2]
        await collector.aclose()

    @pytest.mark.asyncio
    async def test_aclose_before_any_collect_call_does_not_raise(self) -> None:
        from backend.domain.use_cases.stream_whale_alerts import _BatchCollector

        collector = _BatchCollector(self._instant_stream([1]).__aiter__())

        await collector.aclose()  # never collected from -- must not raise
