from __future__ import annotations

import pytest

import backend.core.container as container_module
from backend.core.settings import Settings


@pytest.fixture(autouse=True)
def use_in_memory_storage_for_unit_tests(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Keep unit/API tests independent from a developer's local database,
    and from the background scheduler — any test that opens `TestClient
    (app)` runs the real FastAPI lifespan, which would otherwise start a
    live 30s-interval scheduler loop for the duration of that test.
    """
    if request.node.get_closest_marker("integration") is not None:
        return
    settings = Settings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        enable_scheduler=False,
    )
    monkeypatch.setattr(container_module, "get_settings", lambda: settings)
