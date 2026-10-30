"""EMA cross alerts by Telegram (Lester 2026-10-25 "si" to Telegram; docs/cron_prompts/cron_20261029_jueves_pendientes.txt Section 4).

- telegram_chats: one Telegram chat per Convexa user, linked with a one-time code the user sends to the bot (the app never sees a phone number or a password).
- telegram_link_codes: those one-time codes (10 minutes).
- ema_alert_rules: per user, symbol and timeframe, "tell me when EMA fast crosses EMA slow" (8/20 by default), up / down / both. `last_state` (+1 fast above, -1 below)
  and `last_candle` (start of the last closed candle evaluated) make each crossing fire exactly once, also across restarts.

Additive: new tables only. Read and written by the API (rules, codes) and by backend.ema_alerts_worker (evaluation, linking, sending).

Revision ID: 0051_ema_alerts_telegram
Revises: 0050_minute_strike_flow
Create Date: 2026-10-29
"""

from __future__ import annotations

from alembic import op

revision = "0051_ema_alerts_telegram"
down_revision = "0050_minute_strike_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_chats (
            user_id integer PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            chat_id bigint NOT NULL,
            linked_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_link_codes (
            code text PRIMARY KEY,
            user_id integer NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            expires_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ema_alert_rules (
            id serial PRIMARY KEY,
            user_id integer NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            symbol text NOT NULL,
            timeframe text NOT NULL CHECK (timeframe IN ('1m', '5m', '15m', '1h')),
            fast_period integer NOT NULL DEFAULT 8 CHECK (fast_period BETWEEN 2 AND 400),
            slow_period integer NOT NULL DEFAULT 20 CHECK (slow_period BETWEEN 2 AND 400),
            direction text NOT NULL DEFAULT 'both' CHECK (direction IN ('up', 'down', 'both')),
            enabled boolean NOT NULL DEFAULT true,
            last_state smallint,
            last_candle timestamptz,
            last_fired_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (user_id, symbol, timeframe, fast_period, slow_period),
            CHECK (fast_period < slow_period)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ema_alert_rules")
    op.execute("DROP TABLE IF EXISTS telegram_link_codes")
    op.execute("DROP TABLE IF EXISTS telegram_chats")
