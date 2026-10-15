from __future__ import annotations

import asyncio
import gc
import logging
import time

import pytest

from backend.core import loop_lag_monitor
from backend.core.loop_lag_monitor import GcPause, LoopLagMonitor


def _fast_monitor(**overrides) -> LoopLagMonitor:
    settings = {
        "interval": 0.01,
        "stall_threshold": 0.05,
        "gc_threshold": 0.0,
        "summary_every": 3600.0,
    }
    settings.update(overrides)
    return LoopLagMonitor("test", **settings)


@pytest.mark.asyncio
async def test_a_blocked_event_loop_is_logged_as_a_stall(caplog: pytest.LogCaptureFixture) -> None:
    monitor = _fast_monitor()
    monitor.start()
    try:
        await asyncio.sleep(0.05)
        with caplog.at_level(logging.WARNING, logger=loop_lag_monitor.logger.name):
            time.sleep(0.2)  # noqa: ASYNC251
            await asyncio.sleep(0.05)
    finally:
        await monitor.stop()

    snapshot = monitor.snapshot()
    assert snapshot.stalls >= 1
    assert snapshot.max_lag_seconds >= 0.15
    stalled = [record for record in caplog.records if "Event loop stalled" in record.getMessage()]
    assert stalled
    assert "in test" in stalled[0].getMessage()
    assert "process CPU" in stalled[0].getMessage()


@pytest.mark.asyncio
async def test_a_quiet_loop_reports_no_stall() -> None:
    monitor = _fast_monitor(stall_threshold=0.5)
    monitor.start()
    try:
        await asyncio.sleep(0.15)
    finally:
        await monitor.stop()

    assert monitor.snapshot().stalls == 0


@pytest.mark.asyncio
async def test_a_garbage_collection_pause_is_recorded_and_reported_by_the_task() -> None:
    monitor = _fast_monitor()
    monitor.start()
    try:
        gc.collect()
        await asyncio.sleep(0.05)
    finally:
        await monitor.stop()

    snapshot = monitor.snapshot()
    assert snapshot.gc_pauses >= 1
    assert snapshot.max_gc_pause_seconds > 0


@pytest.mark.asyncio
async def test_stall_logs_are_rate_limited(caplog: pytest.LogCaptureFixture) -> None:
    monitor = _fast_monitor()
    monitor.start()
    try:
        await asyncio.sleep(0.03)
        with caplog.at_level(logging.WARNING, logger=loop_lag_monitor.logger.name):
            for _ in range(loop_lag_monitor.MAX_STALL_LOGS_PER_MINUTE + 5):
                time.sleep(0.08)  # noqa: ASYNC251
                await asyncio.sleep(0.02)
    finally:
        await monitor.stop()

    stalled = [record for record in caplog.records if "Event loop stalled" in record.getMessage()]
    assert len(stalled) <= loop_lag_monitor.MAX_STALL_LOGS_PER_MINUTE
    assert monitor.snapshot().stalls > len(stalled)  # the counter still sees all of them


@pytest.mark.asyncio
async def test_the_summary_line_is_logged_and_the_window_restarts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    monitor = _fast_monitor(summary_every=0.05)
    with caplog.at_level(logging.INFO, logger=loop_lag_monitor.logger.name):
        monitor.start()
        try:
            await asyncio.sleep(0.2)
        finally:
            await monitor.stop()

    summaries = [record for record in caplog.records if "Loop lag in test" in record.getMessage()]
    assert summaries
    assert "stalls" in summaries[0].getMessage()


@pytest.mark.asyncio
async def test_stop_removes_the_gc_callback_and_is_idempotent() -> None:
    monitor = _fast_monitor()
    monitor.start()
    monitor.start()  # a second start does not install a second callback or task
    assert gc.callbacks.count(monitor._on_gc) == 1
    await monitor.stop()
    await monitor.stop()

    assert monitor._on_gc not in gc.callbacks


def test_gc_callback_ignores_a_stop_without_a_start_and_keeps_pauses_bounded() -> None:
    monitor = _fast_monitor()
    monitor._on_gc("stop", {"generation": 2, "collected": 5})  # nothing to measure: no entry
    assert not monitor._gc_pauses

    for _ in range(loop_lag_monitor.GC_PAUSE_BACKLOG + 50):
        monitor._on_gc("start", {})
        monitor._on_gc("stop", {"generation": 1, "collected": 1})

    assert len(monitor._gc_pauses) == loop_lag_monitor.GC_PAUSE_BACKLOG
    assert isinstance(monitor._gc_pauses[0], GcPause)
