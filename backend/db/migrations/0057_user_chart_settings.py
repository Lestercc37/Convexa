"""user_chart_settings: each trader's chart template (Lester 2026-11-06: "que las plantillas de cada trader se guarden para no activar
desde cero todos los días lo mismo").

One row per user: the chart's toggles and choices (heatmap and its figure, GEX profile, smile, flow, cubes, lines, timeframe, gamma tab,
EMAs, cube minimums...) as one JSON object, rewritten whenever the trader changes something (debounced by the page). The page applies it to
every symbol and on every device. Small (a few KB per user).

Revision ID: 0057_user_chart_settings
Revises: 0056_futures_events
Create Date: 2026-11-06
"""

from __future__ import annotations

from alembic import op

revision = "0057_user_chart_settings"
down_revision = "0056_futures_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_chart_settings (
            user_id integer PRIMARY KEY,
            settings jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_chart_settings")
