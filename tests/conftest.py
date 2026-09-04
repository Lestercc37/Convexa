from __future__ import annotations

import pytest

import backend.core.container as container_module
from backend.core.settings import Settings


@pytest.fixture(autouse=True)
def use_in_memory_storage_for_unit_tests(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Keep unit/API tests independent from a developer's local database.

    `enable_scheduler=False` no longer gates anything in backend/main.py's
    own lifespan (the scheduler/whale-alerts-stream/underlying-price-
    stream moved to backend/worker.py, a separate process pytest never
    invokes) -- kept here anyway as the same kill switch for any test
    that builds a container and drives backend/worker.py's `run()`
    directly, without spawning a real second process.
    """
    if request.node.get_closest_marker("integration") is not None:
        return
    settings = Settings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        enable_scheduler=False,
    )
    monkeypatch.setattr(container_module, "get_settings", lambda: settings)
