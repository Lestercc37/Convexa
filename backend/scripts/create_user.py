"""Provision one login account for the dashboard (the owner or one of the
4 teammates) -- run once per person, interactively, so no plaintext
password ever lands in shell history, a CLI argument list, or git.

Usage:
    python -m backend.scripts.create_user --username lester --admin
    python -m backend.scripts.create_user --username teammate1
"""

from __future__ import annotations

import argparse
import getpass

from backend.core.container import build_container
from backend.domain.use_cases.auth import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Grant admin access (can change whale-alert thresholds and screener presets). "
        "Omit for the 4 read-only teammate accounts.",
    )
    args = parser.parse_args()

    container = build_container()
    if container.storage.get_user_by_username(args.username) is not None:
        raise SystemExit(f"A user named {args.username!r} already exists.")

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords did not match.")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")

    password_hash, salt = hash_password(password)
    user = container.storage.create_user(
        username=args.username,
        password_hash=password_hash,
        salt=salt,
        is_admin=args.admin,
    )
    print(f"Created user {user.username!r} (admin={user.is_admin}).")


if __name__ == "__main__":
    main()
