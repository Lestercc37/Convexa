from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest

from backend.core.container import build_container
from backend.core.underlying_price_stream import UnderlyingPriceStreamManager
from backend.domain.entities import MarketPrice, UnderlyingTradeEvent
from backend.domain.underlyings import ACTIVE_UNDERLYINGS

ACTIVE_SYMBOLS = [underlying.symbol for underlying in ACTIVE_UNDERLYINGS]


class _StubStorage:
    def save_market_price(self, price: MarketPrice) -> None:
        pass

    def get_latest_price(self, underlying: str) -> MarketPrice | None:
        return None


class _StubProvider:
    """Immediately-exhausted stream, same shape as MockDataProvider's own
    stream_underlying_trades no-op -- every symbol's task completes on
    its own almost instantly."""

    async def stream_underlying_trades(self, underlying: str) -> AsyncIterator[UnderlyingTradeEvent]:
        if False:
            yield


def _manager_with_stub() -> UnderlyingPriceStreamManager:
    container = replace(
        build_container(),
        market_data_provider=_StubProvider(),
        storage=_StubStorage(),
    )
    return UnderlyingPriceStreamManager(container)


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

    await manager.stop()  # never started -- must not raise

    assert manager._tasks == []


@pytest.mark.asyncio
async def test_stop_clears_every_task() -> None:
    manager = _manager_with_stub()
    manager.start()

    await manager.stop()

    assert manager._tasks == []


@pytest.mark.asyncio
async def test_one_symbols_stream_failure_does_not_crash_the_others() -> None:
    class _FailingProvider:
        async def stream_underlying_trades(self, underlying: str) -> AsyncIterator[UnderlyingTradeEvent]:
            raise RuntimeError(f"stream failed for {underlying}")
            yield  # pragma: no cover -- makes this a real async generator

    container = replace(
        build_container(),
        market_data_provider=_FailingProvider(),
        storage=_StubStorage(),
    )
    manager = UnderlyingPriceStreamManager(container)

    manager.start()
    # Every task fails immediately (RuntimeError raised before any
    # yield), and the supervisor below would retry it forever with
    # real backoff -- stop() must still complete promptly by cancelling
    # each task rather than hanging on one that's mid-retry, and must
    # not let one symbol's permanent failure block clearing any other
    # symbol's task.
    await manager.stop()

    assert manager._tasks == []


@pytest.mark.asyncio
async def test_a_symbols_task_restarts_after_an_exception_without_affecting_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual fix, not just "doesn't crash": a symbol whose stream
    raises must come back on its own, with the same backoff shape/values
    already established for every other ThetaData reconnect in this
    codebase -- confirmed here, not assumed, same
    monkeypatch-asyncio.sleep-and-record pattern
    test_run_backs_off_exponentially_between_reconnect_attempts already
    uses for ThetaTradeStream's own reconnect loop."""
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    class _FlakyOnceProvider:
        def __init__(self) -> None:
            self.calls: dict[str, int] = {}

        async def stream_underlying_trades(
            self, underlying: str
        ) -> AsyncIterator[UnderlyingTradeEvent]:
            self.calls[underlying] = self.calls.get(underlying, 0) + 1
            if underlying == "AAPL" and self.calls[underlying] == 1:
                raise RuntimeError("simulated transient failure")
            if False:
                yield

    provider = _FlakyOnceProvider()
    container = replace(build_container(), market_data_provider=provider, storage=_StubStorage())
    manager = UnderlyingPriceStreamManager(container)

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
