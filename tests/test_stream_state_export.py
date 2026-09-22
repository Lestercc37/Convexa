from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.core.container import build_container
from backend.core.stream_state_export import StreamStateExporter
from backend.domain.entities import FlowEvent, FlowEventType, LatestQuote, Side
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases.flow import SymbolFlowPressure


class _StubProvider:
    def __init__(self, volumes: dict[str, int] | None = None) -> None:
        self._volumes = volumes or {}

    def cumulative_volumes(self) -> dict[str, int]:
        return self._volumes


class _StubStorage:
    def __init__(self) -> None:
        self.saved_volumes: list[dict[str, int]] = []
        self.saved_flow_pressures: list[SymbolFlowPressure] = []

    def save_cumulative_volumes(self, volumes: dict[str, int]) -> None:
        self.saved_volumes.append(volumes)

    def save_symbol_flow_pressure(self, flow: SymbolFlowPressure) -> None:
        self.saved_flow_pressures.append(flow)


def _exporter(provider: _StubProvider, storage: _StubStorage) -> StreamStateExporter:
    container = replace(build_container(), market_data_provider=provider, storage=storage)
    return StreamStateExporter(container)


@pytest.mark.asyncio
async def test_export_once_writes_nonempty_cumulative_volumes() -> None:
    provider = _StubProvider(volumes={"AAPL260220C00200000": 42})
    storage = _StubStorage()
    exporter = _exporter(provider, storage)

    await exporter._export_once()

    assert storage.saved_volumes == [{"AAPL260220C00200000": 42}]


@pytest.mark.asyncio
async def test_export_once_skips_the_volume_write_when_empty() -> None:
    """MockDataProvider (and any process whose stream was never started)
    always returns {} here -- must not fire a write for nothing every
    single export tick."""
    provider = _StubProvider(volumes={})
    storage = _StubStorage()
    exporter = _exporter(provider, storage)

    await exporter._export_once()

    assert storage.saved_volumes == []


@pytest.mark.asyncio
async def test_export_once_persists_real_symbol_flow_from_a_classified_trade() -> None:
    """Uses the container's real WhaleAlertsEngine (not a stub) -- the
    point of this test is that _export_once() reads whatever
    process_trade() actually accumulated, the same live data
    RefreshUnderlyingSnapshotUseCase.execute() used to read directly
    before the scheduler/stream process split (see that use case's own
    comment for why it no longer does)."""
    provider = _StubProvider()
    storage = _StubStorage()
    container = replace(build_container(), market_data_provider=provider, storage=storage)
    container.whale_alerts_engine.process_trade(
        FlowEvent(
            symbol="SPY",
            occ_symbol="SPY260220C00540000",
            as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
            event_type=FlowEventType.UNUSUAL,
            premium=Decimal(1000),
            size=1,
            aggressor_side=Side.UNKNOWN,
        ),
        LatestQuote(bid=Decimal("0.01"), ask=Decimal("0.02"), as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC)),
    )
    exporter = StreamStateExporter(container)

    await exporter._export_once()

    saved_symbols = {flow.symbol for flow in storage.saved_flow_pressures}
    assert "SPY" in saved_symbols
    spy_flow = next(flow for flow in storage.saved_flow_pressures if flow.symbol == "SPY")
    assert spy_flow.net_call_premium == Decimal(1000)


@pytest.mark.asyncio
async def test_export_once_skips_symbols_with_no_flow_yet() -> None:
    """Every ACTIVE_UNDERLYINGS symbol is checked every tick, but
    symbol_flow() returns None for one nothing has traded yet -- must not
    write a placeholder for it."""
    provider = _StubProvider()
    storage = _StubStorage()
    exporter = _exporter(provider, storage)

    await exporter._export_once()

    assert storage.saved_flow_pressures == []


@pytest.mark.asyncio
async def test_start_creates_a_periodic_task() -> None:
    exporter = _exporter(_StubProvider(), _StubStorage())

    exporter.start()

    assert exporter._task is not None
    await exporter.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    exporter = _exporter(_StubProvider(), _StubStorage())

    exporter.start()
    first_task = exporter._task
    exporter.start()

    assert exporter._task is first_task
    await exporter.stop()


@pytest.mark.asyncio
async def test_stop_before_start_is_a_no_op() -> None:
    exporter = _exporter(_StubProvider(), _StubStorage())

    await exporter.stop()  # never started -- must not raise

    assert exporter._task is None


@pytest.mark.asyncio
async def test_run_exports_on_the_configured_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same monkeypatch-asyncio.sleep pattern already established for
    every other timer-driven manager in this package (see
    test_underlying_price_stream.py's own reconnect-backoff test) --
    confirms _run() actually loops and calls _export_once() on its own,
    not just that _export_once() itself works in isolation."""
    sleep_calls: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    provider = _StubProvider(volumes={"AAPL260220C00200000": 1})
    storage = _StubStorage()
    exporter = _exporter(provider, storage)

    exporter.start()
    with pytest.raises(asyncio.CancelledError):
        await exporter._task

    assert sleep_calls == [15, 15, 15]
    assert len(storage.saved_volumes) == 2


def test_active_underlyings_is_nonempty() -> None:
    # Sanity check for this module's own iteration target -- if this
    # were ever empty, every test above would trivially pass for the
    # wrong reason (nothing to iterate, not "handled correctly").
    assert len(ACTIVE_UNDERLYINGS) > 0
