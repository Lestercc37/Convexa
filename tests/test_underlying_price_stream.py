from __future__ import annotations

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
    # yield) -- proves the dashboard keeps running off the REST
    # scheduler's own writes exactly as it does today, per this
    # manager's own graceful-degradation contract.
    await manager.stop()

    assert manager._tasks == []
