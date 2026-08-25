"""Create and seed editable Screener preset settings.

Revision ID: 0017_screener_preset_settings
Revises: 0016_sustained_flow_threshold
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0017_screener_preset_settings"
down_revision = "0016_sustained_flow_threshold"
branch_labels = None
depends_on = None

# Only 3 of the 5 presets have real, editable parameters (task decision) —
# Unusual Options Activity's real config already lives in whale_thresholds
# (PR #69), and Max Pain & Key Levels has no scalar worth thresholding.
# `net_gamma_max` defaults to 0, matching the value that was hardcoded
# before this migration (Negative Gamma Board is inherently about
# negative gamma, so an unconfigured row must not become unbounded).
# `min_magnitude`/`limit` default to null — both exposure-leader presets
# are unconditional rankings today, so "unset" must mean "behave exactly
# as before", not some new default filter.
_SEED_ROWS = (
    ("negative-gamma-board", '{"net_gamma_max": "0"}'),
    ("vanna-exposure-leaders", '{"min_magnitude": null, "limit": null}'),
    ("charm-decay-pressure", '{"min_magnitude": null, "limit": null}'),
)


def upgrade() -> None:
    """Create the table and seed the 3 presets that have real parameters."""
    op.execute(
        """
        CREATE TABLE screener_preset_settings (
            preset text PRIMARY KEY,
            parameters jsonb NOT NULL
        )
        """
    )
    connection = op.get_bind()
    connection.execute(
        text(
            """
            INSERT INTO screener_preset_settings (preset, parameters)
            VALUES (:preset, CAST(:parameters AS jsonb))
            ON CONFLICT (preset) DO UPDATE SET parameters = EXCLUDED.parameters
            """
        ),
        [{"preset": preset, "parameters": parameters} for preset, parameters in _SEED_ROWS],
    )


def downgrade() -> None:
    """Drop the editable Screener preset settings."""
    op.execute("DROP TABLE screener_preset_settings")
