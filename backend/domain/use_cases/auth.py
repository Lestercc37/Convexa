from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from backend.domain.entities import User
from backend.domain.ports import IStorage

# PBKDF2-HMAC-SHA256, stdlib only (hashlib) -- no bcrypt/passlib dependency,
# consistent with the rest of this codebase's domain logic. 310,000
# iterations is OWASP's current minimum recommendation for PBKDF2-SHA256.
PBKDF2_ITERATIONS = 310_000
_SALT_BYTES = 16


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Returns (password_hash, salt), both hex-encoded.

    Generates a fresh random salt when none is given (new user). Pass an
    existing user's own salt to re-derive the same hash for a login
    attempt against it -- see verify_password.
    """
    if salt is None:
        salt = secrets.token_hex(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return derived.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    # Constant-time comparison -- a plain `==` would let an attacker time
    # how many leading bytes matched.
    return secrets.compare_digest(candidate, password_hash)


@dataclass(frozen=True, slots=True)
class AuthenticateUserUseCase:
    storage: IStorage

    def execute(self, username: str, password: str) -> User | None:
        user = self.storage.get_user_by_username(username)
        if user is None:
            return None
        if not verify_password(password, user.password_hash, user.salt):
            return None
        return user
