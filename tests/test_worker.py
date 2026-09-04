from __future__ import annotations

import asyncio

import pytest

import backend.core.container as container_module
import backend.worker as worker_module
from backend.core.settings import Settings


@pytest.mark.asyncio
async def test_run_exits_immediately_when_scheduler_disabled() -> None:
    """The autouse fixture in conftest.py already sets
    enable_scheduler=False for every non-integration test -- confirms
    run() respects that kill switch (same one backend/main.py's lifespan
    used to gate on) instead of starting anything or hanging."""
    await asyncio.wait_for(worker_module.run(), timeout=2)


@pytest.mark.asyncio
async def test_run_starts_all_three_managers_and_stops_cleanly_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MockDataProvider (the default -- see Settings.data_provider),
    enable_scheduler explicitly True this time. Starts run() as a
    background task, confirms all 3 managers are running (their task
    lists are non-empty), then cancels it -- proving run()'s finally
    block stops every manager and disposes both engines without
    hanging, the same shutdown path Ctrl+C drives in real use."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        enable_scheduler=True,
    )
    monkeypatch.setattr(container_module, "get_settings", lambda: settings)

    task = asyncio.create_task(worker_module.run())
    # Give run() enough of the event loop to build the container and
    # call each manager's start() -- all synchronous/fast under
    # MockDataProvider, no real network I/O to wait on.
    await asyncio.sleep(0.2)

    assert not task.done(), "run() returned early instead of running forever"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
