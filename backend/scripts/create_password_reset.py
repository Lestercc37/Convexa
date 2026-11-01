"""Generate a one-time password-reset link for an EXISTING account (Lester 2026-10-31: a teammate forgot his password; a new invite meant a
second account). Same flow as create_invite.py: copy the printed URL and send it however you already talk to them. They open it, pick a new
password and land on the dashboard logged in, keeping their username and access level; you never see or choose their password.

Usage:
    python -m backend.scripts.create_password_reset --username gabriel95 --base-url https://dashboard.convexatrading.com
"""

from __future__ import annotations

import argparse
from datetime import timedelta

from backend.core.container import build_container
from backend.domain.entities import utc_now
from backend.domain.use_cases.auth import generate_invite_token

# Shorter than a signup invite (7 days): a reset link replaces the password of an account that already has access.
RESET_TTL = timedelta(hours=48)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument(
        "--base-url",
        default="http://localhost:3000",
        help="Where the dashboard is actually reachable, e.g. https://dashboard.convexatrading.com",
    )
    args = parser.parse_args()

    container = build_container()
    user = container.storage.get_user_by_username(args.username)
    if user is None:
        raise SystemExit(f"No user named {args.username!r}; for a new account use create_invite.py.")

    token = generate_invite_token()
    container.storage.create_invite(
        token=token,
        username=user.username,
        is_admin=user.is_admin,
        expires_at=utc_now() + RESET_TTL,
        purpose="reset",
    )
    print(f"{args.base_url}/signup?token={token}")
    print("Valid for 48 hours, single use. The username and access level stay the same.")


if __name__ == "__main__":
    main()
