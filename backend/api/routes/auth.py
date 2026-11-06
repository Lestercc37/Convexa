from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response

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
    SessionPayload,
    create_session_token,
    session_cookie_options,
)
from backend.domain.entities import User
from backend.domain.use_cases.errors import NotFoundError, UnauthorizedError

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, container: Container, user: User) -> None:
    token = create_session_token(container.settings.session_secret, user)
    # Cloudflare Tunnel terminates TLS in front of this app, so the
    # browser always sees HTTPS in production -- secure must be True
    # there (QLL_ENVIRONMENT != "development") or the browser silently
    # drops the cookie. False only for local http://localhost dev.
    response.set_cookie(value=token, **session_cookie_options(container.settings.environment != "development"))


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


# Each trader's chart template (Lester 2026-11-06): the page reads it once and writes it back a moment after every change. Any logged-in user,
# only their own row. A JSON object of at most MAX_CHART_SETTINGS_BYTES; the page owns its keys (frontend lib/chart-settings.ts).
MAX_CHART_SETTINGS_BYTES = 32_000


@router.get("/me/chart-settings")
def get_my_chart_settings(request: Request, session: SessionPayload = Depends(require_session)) -> dict:
    container: Container = request.app.state.container
    return {"schema_version": 1, "settings": container.storage.get_user_chart_settings(session.user_id) or {}}


@router.put("/me/chart-settings")
def put_my_chart_settings(body: dict, request: Request, session: SessionPayload = Depends(require_session)) -> dict:
    settings = body.get("settings")
    if not isinstance(settings, dict):
        raise HTTPException(status_code=422, detail="settings must be a JSON object")
    if len(json.dumps(settings)) > MAX_CHART_SETTINGS_BYTES:
        raise HTTPException(status_code=413, detail=f"settings are limited to {MAX_CHART_SETTINGS_BYTES} bytes")
    container: Container = request.app.state.container
    container.storage.save_user_chart_settings(session.user_id, settings)
    return {"schema_version": 1, "saved": True}


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
    return InvitePreviewResponse(username=invite.username, purpose=invite.purpose)


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
