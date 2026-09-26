from __future__ import annotations

from typing import Iterator

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


@pytest.fixture(autouse=True)
def bypass_auth_for_unit_tests(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[None]:
    """Every route test written before the login system (2026-09-25)
    calls TestClient(app) directly with no session cookie -- rather than
    retrofitting every one of those files to log in first, the default
    test posture is a fake, already-authenticated admin session (matches
    the full read+write access those tests exercised before auth
    existed). Uses FastAPI's own dependency_overrides, the standard way
    to fake a Depends() in tests -- not a monkeypatch of the functions
    themselves, so the real require_session/require_admin bodies stay
    completely untested code paths here (test_auth.py, marked
    `real_auth`, is what actually exercises them).

    Two call sites need covering, not just one: most tests import the
    module-level `backend.main.app` singleton (built once, at import
    time), but a few (e.g. test_screener_presets.py) call `create_app()`
    themselves for a fresh instance/container per test. Patching only the
    singleton left those silently unauthenticated (confirmed: a real 401
    from a freshly-built app the singleton-only version never touched).
    """
    if request.node.get_closest_marker("real_auth") is not None:
        yield
        return
    import backend.main as main_module
    from backend.api.deps import require_admin, require_session
    from backend.core.sessions import SessionPayload
    from fastapi.testclient import TestClient

    fake_session = SessionPayload(user_id=0, username="test-user", is_admin=True)

    def _apply_overrides(app: main_module.FastAPI) -> None:
        app.dependency_overrides[require_session] = lambda: fake_session
        app.dependency_overrides[require_admin] = lambda: fake_session

    _apply_overrides(main_module.app)

    # `from backend.main import create_app` (several test files do this)
    # binds that name to the original function object at import time --
    # monkeypatching the attribute on the `backend.main` module afterwards
    # never reaches those already-bound names, so a test's own
    # `create_app()` call would build a fresh, still-unauthenticated app
    # untouched by the override above. TestClient, by contrast, is a
    # single shared class every test file references (however it was
    # imported) -- patching its __init__ to apply the same overrides to
    # whatever `app` it's handed catches every fresh app a test builds,
    # not just the module-level singleton.
    original_init = TestClient.__init__

    def patched_init(self: TestClient, app: main_module.FastAPI, *args: object, **kwargs: object) -> None:
        _apply_overrides(app)
        original_init(self, app, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", patched_init)
    yield
    main_module.app.dependency_overrides.pop(require_session, None)
    main_module.app.dependency_overrides.pop(require_admin, None)
