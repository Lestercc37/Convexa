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
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 days


@dataclass(frozen=True, slots=True)
class SessionPayload:
    user_id: int
    username: str
    is_admin: bool


def create_session_token(secret: str, user: User) -> str:
    payload = {
        "uid": user.id,
        "u": user.username,
        "adm": user.is_admin,
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    body = urlsafe_b64encode(json.dumps(payload).encode("utf-8")).rstrip(b"=")
    signature = hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    return f"{body.decode('ascii')}.{signature}"


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
            user_id=int(payload["uid"]), username=str(payload["u"]), is_admin=bool(payload["adm"])
        )
    except (KeyError, TypeError, ValueError):
        return None
