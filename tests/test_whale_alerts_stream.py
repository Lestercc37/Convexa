from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest

from backend.core.container import build_container
from backend.core.whale_alerts_stream import WhaleAlertsStreamManager
from backend.domain.entities import FlowEvent, QuoteEvent
from backend.domain.underlyings import ACTIVE_UNDERLYINGS

ACTIVE_SYMBOLS = [underlying.symbol for underlying in ACTIVE_UNDERLYINGS]


class _StubEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def process_trade(self, event: object, quote: object) -> tuple[()]:
        self.calls.append((event, quote))
        return ()


class _StubProvider:
    """Immediately-exhausted streams, same shape as MockDataProvider's own
    stream_trades/stream_quotes no-ops — every symbol's task completes on
    its own almost instantly."""

    async def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]:
        if False:
            yield

    async def stream_quotes(self, underlying: str) -> AsyncIterator[QuoteEvent]:
        if False:
            yield


def _manager_with_stub() -> WhaleAlertsStreamManager:
    container = replace(
        build_container(),
        market_data_provider=_StubProvider(),
        whale_alerts_engine=_StubEngine(),
    )
    return WhaleAlertsStreamManager(container)


@pytest.mark.asyncio
async def test_start_creates_one_task_per_active_underlying() -> None:
    manager = _manager_with_stub()

    manager.start()

    assert len(manager._tasks) == len(ACTIVE_SYMBOLS)
    await manager.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    manager = _manager_with_stub()

    manager.start()
    first_tasks = list(manager._tasks)
    manager.start()

    assert manager._tasks == first_tasks
    await manager.stop()


@pytest.mark.asyncio
async def test_stop_before_start_is_a_no_op() -> None:
    manager = _manager_with_stub()

    await manager.stop()  # never started — must not raise

    assert manager._tasks == []


@pytest.mark.asyncio
async def test_stop_clears_every_task() -> None:
    manager = _manager_with_stub()
    manager.start()

    await manager.stop()

    assert manager._tasks == []


@pytest.mark.asyncio
async def test_start_creates_a_dedicated_executor_sized_to_active_symbols() -> None:
    """Fix (2026-09-10): process_trade() used to share asyncio.
    to_thread()'s default executor with the REST scheduler and
    reconcile() -- confirmed live, real market open, that this let the
    scheduler's own concurrent work starve Whale Alerts of threads
    during a real burst, dropping messages. Sized by symbol count, not
    CPU count -- this workload is I/O-bound (a Postgres write), not
    CPU-bound, so every symbol's own trade-consumer task can always get
    an uncontended thread regardless of what else is running."""
    manager = _manager_with_stub()

    manager.start()

    assert manager._executor is not None
    assert manager._executor._max_workers == len(ACTIVE_SYMBOLS)
    await manager.stop()


@pytest.mark.asyncio
async def test_stop_shuts_down_the_executor() -> None:
    manager = _manager_with_stub()
    manager.start()
    executor = manager._executor
    assert executor is not None

    await manager.stop()

    assert manager._executor is None
    # A shutdown ThreadPoolExecutor refuses new work -- the clearest
    # black-box proof shutdown() actually ran, not just that the
    # reference was cleared.
    with pytest.raises(RuntimeError):
        executor.submit(lambda: None)


@pytest.mark.asyncio
async def test_stop_before_start_does_not_touch_a_nonexistent_executor() -> None:
    manager = _manager_with_stub()

    await manager.stop()  # never started -- must not raise

    assert manager._executor is None


@pytest.mark.asyncio
async def test_one_symbols_stream_failure_does_not_crash_the_others() -> None:
    class _FailingProvider:
        async def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]:
            raise RuntimeError(f"stream failed for {underlying}")
            yield  # pragma: no cover -- makes this a real async generator

        async def stream_quotes(self, underlying: str) -> AsyncIterator[QuoteEvent]:
            if False:
                yield

    container = replace(
        build_container(),
        market_data_provider=_FailingProvider(),
        whale_alerts_engine=_StubEngine(),
    )
    manager = WhaleAlertsStreamManager(container)

    manager.start()
    # Every task fails immediately (RuntimeError raised before any
    # yield), and the supervisor below would retry it forever with real
    # backoff -- stop() must still complete promptly by cancelling each
    # task rather than hanging on one that's mid-retry, and must not let
    # one symbol's permanent failure block clearing any other symbol's
    # task.
    await manager.stop()

    assert manager._tasks == []


@pytest.mark.asyncio
async def test_a_symbols_task_restarts_after_an_exception_without_affecting_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual fix (2026-09-10): confirmed missing during the
    ThetaStreamHub backpressure investigation -- a symbol whose stream
    raised used to be logged and left dead forever, unlike
    UnderlyingPriceStreamManager's own identical supervisor. Same
    monkeypatch-asyncio.sleep-and-record pattern that class's own
    equivalent test already uses."""
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    class _FlakyOnceProvider:
        def __init__(self) -> None:
            self.calls: dict[str, int] = {}

        async def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]:
            self.calls[underlying] = self.calls.get(underlying, 0) + 1
            if underlying == "AAPL" and self.calls[underlying] == 1:
                raise RuntimeError("simulated transient failure")
            if False:
                yield

        async def stream_quotes(self, underlying: str) -> AsyncIterator[QuoteEvent]:
            if False:
                yield

    provider = _FlakyOnceProvider()
    container = replace(
        build_container(), market_data_provider=provider, whale_alerts_engine=_StubEngine()
    )
    manager = WhaleAlertsStreamManager(container)

    manager.start()
    # AAPL's task: raises -> sleeps (patched, instant) -> retries -> a
    # clean second call completes it. Every other symbol's task
    # completes on its own first, unaffected, real attempt.
    await asyncio.wait_for(asyncio.gather(*manager._tasks), timeout=5)

    assert provider.calls["AAPL"] == 2
    assert sleep_calls == [2]  # RECONNECT_BASE_DELAY_SECONDS, the shared convention
    other_symbols = [s for s in ACTIVE_SYMBOLS if s != "AAPL"]
    assert all(provider.calls[symbol] == 1 for symbol in other_symbols)

    await manager.stop()
