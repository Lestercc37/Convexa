"""Generate a one-time signup link for a teammate -- no email needed,
just copy the printed URL and send it however you already talk to them
(WhatsApp, text, in person). They open it, pick their own password, and
land on the dashboard already logged in; you never see or choose their
password yourself.

Usage:
    python -m backend.scripts.create_invite --username teammate1 --base-url https://dashboard.convexatrading.com
    python -m backend.scripts.create_invite --username teammate2 --admin --base-url https://dashboard.convexatrading.com
"""

from __future__ import annotations

import argparse
from datetime import timedelta

from backend.core.container import build_container
from backend.domain.entities import utc_now
from backend.domain.use_cases.auth import generate_invite_token

INVITE_TTL = timedelta(days=7)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Grant admin access (can change whale-alert thresholds and screener presets). "
        "Omit for the 4 read-only teammate accounts.",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:3000",
        help="Where the dashboard is actually reachable, e.g. https://dashboard.convexatrading.com",
    )
    args = parser.parse_args()

    container = build_container()
    if container.storage.get_user_by_username(args.username) is not None:
        raise SystemExit(f"A user named {args.username!r} already exists.")

    token = generate_invite_token()
    container.storage.create_invite(
        token=token,
        username=args.username,
        is_admin=args.admin,
        expires_at=utc_now() + INVITE_TTL,
    )
    print(f"{args.base_url}/signup?token={token}")
    print("Valid for 7 days, single use.")


if __name__ == "__main__":
    main()
