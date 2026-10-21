"""Sliding session renewal (2026-10-21, Lester): a pure ASGI middleware that hands a fresh session cookie to a logged-in browser once its token is older than a day.

It sits OUTSIDE the routes on purpose: some routes return their own Response objects, and a FastAPI dependency's Response is dropped in that case, so a dependency could not renew every
route. Only HTTP requests with a valid, not-fresh token and a successful (< 400) response get a cookie; login / logout (which set or clear the cookie themselves), bad tokens and
errors are left alone. The cookie attributes come from sessions.session_cookie_options, the same helper login uses.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.core.sessions import SESSION_COOKIE_NAME, renewed_session_token, session_cookie_options


class SessionRenewalMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = Request(scope).cookies.get(SESSION_COOKIE_NAME)
        container = getattr(scope["app"].state, "container", None)
        if token is None or container is None:
            await self.app(scope, receive, send)
            return

        async def send_with_renewal(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] < 400:
                headers = MutableHeaders(scope=message)
                already_set = any(SESSION_COOKIE_NAME in value for value in headers.getlist("set-cookie"))
                if not already_set:
                    fresh = renewed_session_token(container.settings.session_secret, token)
                    if fresh is not None:
                        carrier = Response()
                        carrier.set_cookie(value=fresh, **session_cookie_options(container.settings.environment != "development"))
                        for value in carrier.headers.getlist("set-cookie"):
                            headers.append("set-cookie", value)
            await send(message)

        await self.app(scope, receive, send_with_renewal)
