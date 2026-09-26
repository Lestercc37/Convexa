from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from backend.api.deps import require_session
from backend.api.schemas import (
    AcceptInviteRequest,
    InvitePreviewResponse,
    LoginRequest,
    SessionUserResponse,
)
from backend.core.container import Container
from backend.core.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    SessionPayload,
    create_session_token,
)
from backend.domain.entities import User
from backend.domain.use_cases.errors import NotFoundError, UnauthorizedError

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, container: Container, user: User) -> None:
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


@router.post("/login", response_model=SessionUserResponse)
def login(body: LoginRequest, request: Request, response: Response) -> SessionUserResponse:
    container: Container = request.app.state.container
    user = container.authenticate_user_use_case.execute(body.username, body.password)
    if user is None:
        raise UnauthorizedError("Invalid username or password")
    _set_session_cookie(response, container, user)
    return SessionUserResponse(username=user.username, is_admin=user.is_admin)


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me", response_model=SessionUserResponse)
def me(session: SessionPayload = Depends(require_session)) -> SessionUserResponse:
    return SessionUserResponse(username=session.username, is_admin=session.is_admin)


@router.get("/invites/{token}", response_model=InvitePreviewResponse)
def preview_invite(token: str, request: Request) -> InvitePreviewResponse:
    """Public (no session) -- lets the signup page show who this link is
    for before the visitor has an account of their own."""
    container: Container = request.app.state.container
    invite = container.storage.get_invite_by_token(token)
    if invite is None or not invite.is_valid:
        raise NotFoundError("Invite not found, already used, or expired")
    return InvitePreviewResponse(username=invite.username)


@router.post("/accept-invite", response_model=SessionUserResponse)
def accept_invite(
    body: AcceptInviteRequest, request: Request, response: Response
) -> SessionUserResponse:
    """Public (no session) -- redeems a one-time link into a real account
    and logs the new user in immediately, same as /login."""
    container: Container = request.app.state.container
    user = container.accept_invite_use_case.execute(body.token, body.password)
    if user is None:
        raise NotFoundError("Invite not found, already used, or expired")
    _set_session_cookie(response, container, user)
    return SessionUserResponse(username=user.username, is_admin=user.is_admin)
