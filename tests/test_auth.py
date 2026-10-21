from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend.adapters.storage.memory import InMemoryStorage
from backend.core.sessions import create_session_token, verify_session_token
from backend.domain.entities import User, utc_now
from backend.domain.use_cases.auth import (
    AcceptInviteUseCase,
    AuthenticateUserUseCase,
    generate_invite_token,
    hash_password,
    verify_password,
)
from backend.main import create_app

pytestmark = pytest.mark.real_auth

SECRET = "test-secret-do-not-use-in-real-life"


def _make_user(username: str = "lester", is_admin: bool = False) -> User:
    password_hash, salt = hash_password("correct horse battery staple")
    return User(
        id=1, username=username, password_hash=password_hash, salt=salt,
        is_admin=is_admin, created_at=utc_now(),
    )


def test_hash_password_round_trips_and_rejects_the_wrong_password() -> None:
    password_hash, salt = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", password_hash, salt)
    assert not verify_password("wrong password", password_hash, salt)


def test_hash_password_generates_a_fresh_salt_each_time_by_default() -> None:
    hash_a, salt_a = hash_password("same password")
    hash_b, salt_b = hash_password("same password")
    assert salt_a != salt_b
    assert hash_a != hash_b


def test_authenticate_user_use_case_accepts_correct_and_rejects_wrong_password() -> None:
    storage = InMemoryStorage()
    password_hash, salt = hash_password("correct horse battery staple")
    storage.create_user(username="lester", password_hash=password_hash, salt=salt, is_admin=True)
    use_case = AuthenticateUserUseCase(storage=storage)

    assert use_case.execute("lester", "correct horse battery staple") is not None
    assert use_case.execute("lester", "wrong password") is None
    assert use_case.execute("nobody", "anything") is None


def test_session_token_round_trips() -> None:
    user = _make_user(is_admin=True)
    token = create_session_token(SECRET, user)
    session = verify_session_token(SECRET, token)
    assert session is not None
    assert session.user_id == user.id
    assert session.username == user.username
    assert session.is_admin is True


def test_session_token_rejects_tampering() -> None:
    user = _make_user()
    token = create_session_token(SECRET, user)
    body, signature = token.split(".", 1)
    tampered = f"{body}x.{signature}"
    assert verify_session_token(SECRET, tampered) is None


def test_session_token_rejects_wrong_secret() -> None:
    user = _make_user()
    token = create_session_token(SECRET, user)
    assert verify_session_token("a different secret entirely", token) is None


def test_session_token_rejects_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.core.sessions as sessions_module

    monkeypatch.setattr(sessions_module, "SESSION_TTL_SECONDS", -1)
    user = _make_user()
    token = create_session_token(SECRET, user)
    assert verify_session_token(SECRET, token) is None


def test_login_sets_a_cookie_and_gates_protected_routes_without_one() -> None:
    app = create_app()
    with TestClient(app) as client:
        storage = app.state.container.storage
        password_hash, salt = hash_password("correct horse battery staple")
        storage.create_user(username="lester", password_hash=password_hash, salt=salt, is_admin=True)

        no_session = client.get("/api/v1/underlyings")
        assert no_session.status_code == 401

        wrong_password = client.post(
            "/api/v1/auth/login", json={"username": "lester", "password": "nope"}
        )
        assert wrong_password.status_code == 401

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "lester", "password": "correct horse battery staple"},
        )
        assert login.status_code == 200
        assert login.json() == {"username": "lester", "is_admin": True}

        with_session = client.get("/api/v1/underlyings")
        assert with_session.status_code == 200

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json() == {"username": "lester", "is_admin": True}

        client.post("/api/v1/auth/logout")
        after_logout = client.get("/api/v1/underlyings")
        assert after_logout.status_code == 401


def test_websocket_route_accepts_a_real_logged_in_session() -> None:
    """require_session_ws, not require_session -- confirmed live,
    2026-09-28: every /ws/market/{symbol} connection all day failed with
    a 500 (`TypeError: require_session() missing 1 required positional
    argument: 'request'`) because market_stream_router was gated with
    plain require_session, which FastAPI can never satisfy for a
    websocket ASGI scope. Every unit test for this route ran under
    bypass_auth_for_unit_tests' dependency_overrides, which replaces the
    dependency wholesale and so never actually called its body -- this
    is the one test (real_auth, no override) that would have caught it,
    same gap bypass_auth_for_unit_tests' own docstring already flags for
    require_session/require_admin."""
    app = create_app()
    with TestClient(app) as client:
        storage = app.state.container.storage
        password_hash, salt = hash_password("correct horse battery staple")
        storage.create_user(username="lester", password_hash=password_hash, salt=salt, is_admin=True)
        client.post(
            "/api/v1/auth/login",
            json={"username": "lester", "password": "correct horse battery staple"},
        )

        with client.websocket_connect("/api/v1/ws/market/SPY") as websocket:
            payload = '{"symbol": "SPY", "price": "552.25", "as_of": "2026-09-05T14:30:00+00:00"}'
            app.state.container.price_notification_hub.publish("SPY", payload)
            received = websocket.receive_text()

        assert received == payload


def test_websocket_route_rejects_a_connection_with_no_session() -> None:
    from starlette.websockets import WebSocketDisconnect

    app = create_app()
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect("/api/v1/ws/market/SPY"):
                pass
        assert excinfo.value.code == 1008


def test_non_admin_session_gets_403_on_admin_only_routes() -> None:
    app = create_app()
    with TestClient(app) as client:
        storage = app.state.container.storage
        password_hash, salt = hash_password("teammate password")
        storage.create_user(
            username="teammate1", password_hash=password_hash, salt=salt, is_admin=False
        )
        client.post(
            "/api/v1/auth/login", json={"username": "teammate1", "password": "teammate password"}
        )

        readable = client.get("/api/v1/whale-thresholds")
        assert readable.status_code == 200

        blocked = client.patch(
            "/api/v1/whale-thresholds/SPY",
            json={
                "unusual_min": 40000,
                "whale_min": 150000,
                "unusual_multiplier": 3.0,
                "whale_multiplier": 6.0,
                "sustained_flow_min": 500000,
            },
        )
        assert blocked.status_code == 403


def test_accept_invite_use_case_creates_the_user_with_the_invites_own_fields() -> None:
    storage = InMemoryStorage()
    token = generate_invite_token()
    storage.create_invite(
        token=token, username="teammate1", is_admin=True, expires_at=utc_now() + timedelta(days=7)
    )
    use_case = AcceptInviteUseCase(storage=storage)

    user = use_case.execute(token, "a brand new password")

    assert user is not None
    assert user.username == "teammate1"
    assert user.is_admin is True
    assert storage.get_user_by_username("teammate1") is not None


def test_accept_invite_use_case_rejects_an_already_used_invite() -> None:
    storage = InMemoryStorage()
    token = generate_invite_token()
    storage.create_invite(
        token=token, username="teammate1", is_admin=False, expires_at=utc_now() + timedelta(days=7)
    )
    use_case = AcceptInviteUseCase(storage=storage)
    assert use_case.execute(token, "first password") is not None

    # Same token again -- must not create a second account or let anyone
    # else redeem an already-consumed link.
    assert use_case.execute(token, "a different password") is None


def test_accept_invite_use_case_rejects_an_expired_invite() -> None:
    storage = InMemoryStorage()
    token = generate_invite_token()
    storage.create_invite(
        token=token, username="teammate1", is_admin=False, expires_at=utc_now() - timedelta(seconds=1)
    )
    use_case = AcceptInviteUseCase(storage=storage)

    assert use_case.execute(token, "a password") is None


def test_accept_invite_use_case_rejects_an_unknown_token() -> None:
    storage = InMemoryStorage()
    use_case = AcceptInviteUseCase(storage=storage)

    assert use_case.execute("not-a-real-token", "a password") is None


def test_signup_routes_are_public_and_accept_invite_logs_the_user_in() -> None:
    app = create_app()
    with TestClient(app) as client:
        storage = app.state.container.storage
        token = generate_invite_token()
        storage.create_invite(
            token=token, username="teammate1", is_admin=False,
            expires_at=utc_now() + timedelta(days=7),
        )

        # Both routes must be reachable with no session cookie at all.
        preview = client.get(f"/api/v1/auth/invites/{token}")
        assert preview.status_code == 200
        assert preview.json() == {"username": "teammate1"}

        accept = client.post(
            "/api/v1/auth/accept-invite", json={"token": token, "password": "a fresh password"}
        )
        assert accept.status_code == 200
        assert accept.json() == {"username": "teammate1", "is_admin": False}

        # accept-invite logged them in immediately, same as /login.
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "teammate1"

        # The link is single-use -- a second redemption must fail.
        second_attempt = client.post(
            "/api/v1/auth/accept-invite", json={"token": token, "password": "whatever"}
        )
        assert second_attempt.status_code == 404


def test_preview_invite_404s_for_an_unknown_token() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/auth/invites/not-a-real-token")
        assert response.status_code == 404


# --- sliding sessions (2026-10-21) -------------------------------------------------------------------------------------------------------------------------------------------------


def _aged_token(age_seconds: float) -> str:
    """A valid token issued `age_seconds` ago (the helper bypasses the clock by signing the payload the way create_session_token does)."""
    import time

    import backend.core.sessions as sessions_module

    user = _make_user(is_admin=True)
    payload = {"uid": user.id, "u": user.username, "adm": user.is_admin, "exp": int(time.time() - age_seconds) + sessions_module.SESSION_TTL_SECONDS}
    return sessions_module._sign(SECRET, payload)


def test_session_lasts_thirty_days_without_use() -> None:
    import backend.core.sessions as sessions_module

    assert sessions_module.SESSION_TTL_SECONDS == 30 * 24 * 3600
    session = verify_session_token(SECRET, create_session_token(SECRET, _make_user()))
    assert session is not None and abs(session.expires_at - (__import__("time").time() + 30 * 24 * 3600)) < 5


def test_a_token_is_renewed_only_when_older_than_a_day_and_keeps_the_same_user() -> None:
    from backend.core.sessions import renewed_session_token

    assert renewed_session_token(SECRET, _aged_token(60)) is None  # a minute old: fresh
    assert renewed_session_token(SECRET, _aged_token(23 * 3600)) is None
    fresh = renewed_session_token(SECRET, _aged_token(25 * 3600))
    assert fresh is not None
    session = verify_session_token(SECRET, fresh)
    assert session is not None and (session.user_id, session.username, session.is_admin) == (1, "lester", True)
    assert renewed_session_token(SECRET, fresh) is None  # the new one is fresh again


def test_a_bad_or_expired_token_is_never_renewed() -> None:
    from backend.core.sessions import renewed_session_token

    assert renewed_session_token(SECRET, "not.a.token") is None
    assert renewed_session_token("another secret", _aged_token(25 * 3600)) is None
    assert renewed_session_token(SECRET, _aged_token(31 * 24 * 3600)) is None  # already expired


def test_an_old_14_day_cookie_gets_a_30_day_one_back_and_the_attributes_match_login() -> None:
    app = create_app()
    with TestClient(app) as client:
        storage = app.state.container.storage
        password_hash, salt = hash_password("correct horse battery staple")
        storage.create_user(username="lester", password_hash=password_hash, salt=salt, is_admin=True)
        secret = app.state.container.settings.session_secret
        login = client.post("/api/v1/auth/login", json={"username": "lester", "password": "correct horse battery staple"})
        login_cookie = login.headers["set-cookie"]

        import time

        import backend.core.sessions as sessions_module

        old = sessions_module._sign(secret, {"uid": 1, "u": "lester", "adm": True, "exp": int(time.time()) + 13 * 24 * 3600})  # a 14-day token, one day used
        client.cookies.set(sessions_module.SESSION_COOKIE_NAME, old)
        response = client.get("/api/v1/underlyings")
        assert response.status_code == 200
        renewed = response.headers.get("set-cookie")
        assert renewed is not None and sessions_module.SESSION_COOKIE_NAME in renewed
        new_token = renewed.split(";", 1)[0].split("=", 1)[1]
        session = verify_session_token(secret, new_token)
        assert session is not None and session.expires_at > time.time() + 29 * 24 * 3600
        # Same attributes as the login cookie (everything after the value).
        assert sorted(part.strip().lower().split("=")[0] for part in renewed.split(";")[1:]) == sorted(part.strip().lower().split("=")[0] for part in login_cookie.split(";")[1:])

        # The next request carries the fresh cookie: nothing more to renew.
        client.cookies.set(sessions_module.SESSION_COOKIE_NAME, new_token)
        assert "set-cookie" not in client.get("/api/v1/underlyings").headers


def test_no_cookie_is_set_for_a_fresh_session_a_failed_request_or_logout() -> None:
    app = create_app()
    with TestClient(app) as client:
        storage = app.state.container.storage
        password_hash, salt = hash_password("correct horse battery staple")
        storage.create_user(username="lester", password_hash=password_hash, salt=salt, is_admin=True)
        client.post("/api/v1/auth/login", json={"username": "lester", "password": "correct horse battery staple"})
        assert "set-cookie" not in client.get("/api/v1/underlyings").headers  # just logged in: fresh

        import time

        import backend.core.sessions as sessions_module

        secret = app.state.container.settings.session_secret
        old = sessions_module._sign(secret, {"uid": 1, "u": "lester", "adm": True, "exp": int(time.time()) + 13 * 24 * 3600})
        client.cookies.set(sessions_module.SESSION_COOKIE_NAME, old)
        missing = client.get("/api/v1/gamma/NOPE-DOES-NOT-EXIST")
        assert missing.status_code >= 400 and "set-cookie" not in missing.headers  # failed request: leave the cookie alone
        out = client.post("/api/v1/auth/logout")
        assert out.headers["set-cookie"].count(sessions_module.SESSION_COOKIE_NAME) == 1  # only the deletion, no renewed cookie next to it
        assert "Max-Age=0" in out.headers["set-cookie"] or "expires" in out.headers["set-cookie"].lower()
