"""Create users -- the login system backing read-only team access to the
dashboard (5 accounts: the owner plus 4 teammates, per the on-premise
server migration). password_hash/salt use hashlib.pbkdf2_hmac (stdlib
only, no bcrypt/passlib dependency, consistent with the rest of this
codebase's minimal-dependency domain logic) -- see backend/domain/use_cases/auth.py.
is_admin gates the few mutating endpoints (whale-thresholds, screener
presets, the internal trigger-calculation route); teammates default to
False, genuinely read-only at the API level, not just by UI convention.

Revision ID: 0031_create_users_table
Revises: 0030_nullable_walls
Create Date: 2026-09-25
"""

from __future__ import annotations

from alembic import op

revision = "0031_create_users_table"
down_revision = "0030_nullable_walls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id bigserial PRIMARY KEY,
            username text NOT NULL UNIQUE,
            password_hash text NOT NULL,
            salt text NOT NULL,
            is_admin boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS users")
