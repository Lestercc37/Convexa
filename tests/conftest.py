from __future__ import annotations

import pytest

import backend.core.container as container_module
from backend.core.settings import Settings


@pytest.fixture(autouse=True)
def use_in_memory_storage_for_unit_tests(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Keep unit/API tests independent from a developer's local database."""
    if request.node.get_closest_marker("integration") is not None:
        return
    settings = Settings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
    )
    monkeypatch.setattr(container_module, "get_settings", lambda: settings)
