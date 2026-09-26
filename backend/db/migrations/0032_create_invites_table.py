"""Create invites -- one-time signup links so the owner can provision the
4 teammate accounts without knowing their email addresses or coordinating
passwords over a call (backend/scripts/create_invite.py generates the
link, the teammate picks their own password at /signup). username and
is_admin are fixed by the owner at creation time, not chosen by whoever
redeems the token.

Revision ID: 0032_create_invites_table
Revises: 0031_create_users_table
Create Date: 2026-09-26
"""

from __future__ import annotations

from alembic import op

revision = "0032_create_invites_table"
down_revision = "0031_create_users_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS invites (
            id bigserial PRIMARY KEY,
            token text NOT NULL UNIQUE,
            username text NOT NULL,
            is_admin boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            used_at timestamptz
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invites")
