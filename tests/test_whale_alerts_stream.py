from __future__ import annotations

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
