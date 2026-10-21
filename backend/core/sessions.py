"""Signed session cookies -- stdlib-only (hmac/hashlib/json), no
itsdangerous/Starlette SessionMiddleware dependency, same "roll your own
small thing instead of a new dependency" call as the PBKDF2 password
hashing in backend/domain/use_cases/auth.py.

The token embeds the whole session payload (user id, username, is_admin,
expiry) and is HMAC-signed with Settings.session_secret -- verifying it
is a pure signature/expiry check, no database round-trip needed on every
request. Tampering with the payload invalidates the signature; there is
no server-side session store to revoke early (logging out just clears
the cookie client-side), which is an accepted tradeoff for a small,
trusted team, not a general-purpose session system.

Sliding expiry (2026-10-21, Lester): a session lasts 30 days WITHOUT USE; every authenticated request made more than a day after the
token was issued gets a fresh 30-day cookie back (see backend/api/session_renewal.py), so someone who uses the dashboard never has to
log in again, while a cookie nobody uses stops working after 30 days.
"""

from __future__ import annotations

import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256

from backend.domain.entities import User

SESSION_COOKIE_NAME = "convexa_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days without use
# A token is re-issued once it is older than this (so a busy dashboard does not set a new cookie on every poll).
SESSION_RENEW_AFTER_SECONDS = 60 * 60 * 24


@dataclass(frozen=True, slots=True)
class SessionPayload:
    user_id: int
    username: str
    is_admin: bool
    # Unix time at which the token stops being valid (0 when unknown).
    expires_at: int = 0


def _sign(secret: str, payload: dict) -> str:
    body = urlsafe_b64encode(json.dumps(payload).encode("utf-8")).rstrip(b"=")
    signature = hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    return f"{body.decode('ascii')}.{signature}"


def create_session_token(secret: str, user: User) -> str:
    return _sign(
        secret,
        {"uid": user.id, "u": user.username, "adm": user.is_admin, "exp": int(time.time()) + SESSION_TTL_SECONDS},
    )


def renewed_session_token(secret: str, token: str, now: float | None = None) -> str | None:
    """A fresh token for the same user when `token` is valid but older than SESSION_RENEW_AFTER_SECONDS; None when it is invalid, expired or still fresh."""
    session = verify_session_token(secret, token)
    if session is None:
        return None
    moment = time.time() if now is None else now
    if session.expires_at - moment >= SESSION_TTL_SECONDS - SESSION_RENEW_AFTER_SECONDS:
        return None
    return _sign(
        secret,
        {"uid": session.user_id, "u": session.username, "adm": session.is_admin, "exp": int(moment) + SESSION_TTL_SECONDS},
    )


def session_cookie_options(secure: bool) -> dict:
    """The attributes of the session cookie, shared by login and by the sliding renewal so they can never differ."""
    return {"key": SESSION_COOKIE_NAME, "max_age": SESSION_TTL_SECONDS, "httponly": True, "samesite": "lax", "secure": secure}


def verify_session_token(secret: str, token: str) -> SessionPayload | None:
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        return None
    expected_signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    try:
        return SessionPayload(
            user_id=int(payload["uid"]),
            username=str(payload["u"]),
            is_admin=bool(payload["adm"]),
            expires_at=int(payload["exp"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
