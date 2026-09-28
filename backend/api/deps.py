from __future__ import annotations

from fastapi import Depends, Request, WebSocket, WebSocketException, status

from backend.core.container import Container
from backend.core.sessions import SESSION_COOKIE_NAME, SessionPayload, verify_session_token
from backend.domain.use_cases.errors import ForbiddenError, UnauthorizedError


def require_session(request: Request) -> SessionPayload:
    """FastAPI dependency gating every route that needs a logged-in user
    -- attach via a router's own `dependencies=[Depends(require_session)]`
    (see backend/api/routes/__init__.py), not per-route, so a new route
    added to an already-gated router can't accidentally end up public."""
    container: Container = request.app.state.container
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None:
        raise UnauthorizedError("Not logged in")
    session = verify_session_token(container.settings.session_secret, token)
    if session is None:
        raise UnauthorizedError("Session expired or invalid")
    return session


def require_session_ws(websocket: WebSocket) -> SessionPayload:
    """require_session's own logic, for WebSocket routes only -- a plain
    `Depends(require_session)` on a websocket route fails outright
    (confirmed live, 2026-09-28: every /ws/market/{symbol} connection
    all day rejected with a 500 before ever reaching accept(), logged as
    `TypeError: require_session() missing 1 required positional
    argument: 'request'`). FastAPI/Starlette never constructs a `Request`
    for a websocket ASGI scope, so a dependency typed on `Request` can't
    resolve there regardless of the router's own `dependencies=` wiring
    -- it needs its own `WebSocket`-typed version instead. Reads from
    `websocket.cookies` (same cookie jar, different attribute name) and
    raises `WebSocketException`, not `UnauthorizedError` -- the
    QllError -> JSONResponse handler in main.py only ever runs for HTTP
    responses, so an `UnauthorizedError` raised here would itself crash
    the same way instead of closing the socket cleanly.
    market_stream.py's route accepts the connection before this can
    matter for delivering the actual price ticks, but WebSocketException
    raised from a dependency is closed by FastAPI before accept() is
    reached, same as the HTTP 401 require_session would have produced."""
    container: Container = websocket.app.state.container
    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if token is None:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Not logged in")
    session = verify_session_token(container.settings.session_secret, token)
    if session is None:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION, reason="Session expired or invalid"
        )
    return session


def require_admin(session: SessionPayload = Depends(require_session)) -> SessionPayload:
    """Layered on top of require_session -- the few mutating routes
    (whale-thresholds, screener presets, the internal trigger-calculation
    route) use this instead, so a teammate's valid but non-admin session
    still gets a real 403, not just a UI convention."""
    if not session.is_admin:
        raise ForbiddenError("Admin access required")
    return session
