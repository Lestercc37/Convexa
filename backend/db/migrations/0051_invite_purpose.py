"""Invites get a purpose: 'signup' (a new account, as before) or 'reset' (a new password for an EXISTING account, Lester 2026-10-31: a teammate
forgot his password and the only way back was a second account). Same one-time link and /signup page; the owner generates it with
backend/scripts/create_password_reset.py. Existing rows stay 'signup'.

Revision ID: 0051_invite_purpose
Revises: 0050_minute_strike_flow
Create Date: 2026-10-31
"""

from __future__ import annotations

from alembic import op

revision = "0051_invite_purpose"
down_revision = "0050_minute_strike_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE invites ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT 'signup'")
    op.execute(
        """
        DO $$ BEGIN
            ALTER TABLE invites ADD CONSTRAINT invites_purpose_check CHECK (purpose IN ('signup', 'reset'));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE invites DROP CONSTRAINT IF EXISTS invites_purpose_check")
    op.execute("ALTER TABLE invites DROP COLUMN IF EXISTS purpose")
