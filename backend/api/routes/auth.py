from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from backend.api.deps import require_session
from backend.api.schemas import LoginRequest, SessionUserResponse
from backend.core.container import Container
from backend.core.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    SessionPayload,
    create_session_token,
)
from backend.domain.use_cases.errors import UnauthorizedError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=SessionUserResponse)
def login(body: LoginRequest, request: Request, response: Response) -> SessionUserResponse:
    container: Container = request.app.state.container
    user = container.authenticate_user_use_case.execute(body.username, body.password)
    if user is None:
        raise UnauthorizedError("Invalid username or password")
    token = create_session_token(container.settings.session_secret, user)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # Cloudflare Tunnel terminates TLS in front of this app, so the
        # browser always sees HTTPS in production -- this must be True
        # there (QLL_ENVIRONMENT != "development") or the browser silently
        # drops the cookie. False only for local http://localhost dev.
        secure=container.settings.environment != "development",
    )
    return SessionUserResponse(username=user.username, is_admin=user.is_admin)


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me", response_model=SessionUserResponse)
def me(session: SessionPayload = Depends(require_session)) -> SessionUserResponse:
    return SessionUserResponse(username=session.username, is_admin=session.is_admin)
