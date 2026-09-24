from __future__ import annotations

import asyncio

import pytest

import backend.core.container as container_module
import backend.whale_alerts_worker as whale_alerts_worker_module
from backend.core.settings import Settings


@pytest.mark.asyncio
async def test_run_exits_immediately_when_scheduler_disabled() -> None:
    """The autouse fixture in conftest.py already sets
    enable_scheduler=False for every non-integration test -- confirms
    run() respects that kill switch instead of starting anything or
    connecting to a relay that doesn't exist in this test."""
    await asyncio.wait_for(whale_alerts_worker_module.run(), timeout=2)


@pytest.mark.asyncio
async def test_run_starts_the_manager_and_stops_cleanly_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MockDataProvider (the default), enable_scheduler explicitly True.
    No real relay server is listening on the configured port in this
    test -- RelayDataProvider's own reconnect loop just keeps retrying
    in the background (same as a real disconnected relay client would),
    which must never stop run() itself from starting cleanly or
    stopping cleanly on cancellation -- the same shutdown path Ctrl+C
    drives in real use."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        enable_scheduler=True,
        # port=0: nothing is listening here in this test regardless (no
        # backend.worker relay server running), but avoids ever binding
        # to -- or trying to connect to -- the real production port.
        whale_alerts_relay_port=0,
    )
    monkeypatch.setattr(container_module, "get_settings", lambda: settings)

    task = asyncio.create_task(whale_alerts_worker_module.run())
    # Give run() enough of the event loop to build the container, start
    # the relay client's reconnect task, and call the manager's start()
    # -- all synchronous/fast under MockDataProvider, no real network
    # I/O to wait on (the relay connection attempt fails fast: nothing
    # is listening on port 0).
    await asyncio.sleep(0.2)

    assert not task.done(), "run() returned early instead of running forever"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
