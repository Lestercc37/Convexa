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

    def process_trade(self, event: FlowEvent, quote: LatestQuote | None) -> tuple[()]:
        self.calls.append((event, quote))
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
