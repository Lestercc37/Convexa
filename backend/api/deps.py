from __future__ import annotations

from fastapi import Depends, Request

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


def require_admin(session: SessionPayload = Depends(require_session)) -> SessionPayload:
    """Layered on top of require_session -- the few mutating routes
    (whale-thresholds, screener presets, the internal trigger-calculation
    route) use this instead, so a teammate's valid but non-admin session
    still gets a real 403, not just a UI convention."""
    if not session.is_admin:
        raise ForbiddenError("Admin access required")
    return session
