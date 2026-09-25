"""Add a `view` column to gamma_aggregates/gamma_aggregate_items and widen
both tables' primary keys to include it -- lets a symbol carry a
Structural (the existing computation, unchanged) and a Tactical (new,
0-2 DTE) GammaAggregate side by side for the same (underlying, time)
instead of one silently overwriting the other under the old 2-column key.

Revision ID: 0029_gamma_aggregate_view
Revises: 0028_cumulative_volume
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_gamma_aggregate_view"
down_revision = "0028_cumulative_volume"
branch_labels = None
depends_on = None

# Confirmed against the real database before writing this migration (see
# 0014_create_gamma_aggregate_items's own comment: the FK from
# gamma_aggregate_items back to gamma_aggregates was created inline
# inside a bare CREATE TABLE, unnamed, so Postgres auto-generated this
# name -- it is NOT guessable from the migration history alone):
# SELECT conname FROM pg_constraint WHERE conrelid =
# 'gamma_aggregate_items'::regclass AND contype = 'f';
_ITEMS_FK_NAME = "gamma_aggregate_items_underlying_id_time_fkey"


def upgrade() -> None:
    """Add `view` (default 'structural', backfilling every existing row
    exactly as it already behaved), then widen both tables' PKs to
    include it. gamma_aggregates' PK was named explicitly in 0014
    ("gamma_aggregates_pkey"); gamma_aggregate_items' PK and FK were both
    created inline by a bare CREATE TABLE in that same migration, so
    Postgres auto-generated their names ("gamma_aggregate_items_pkey",
    _ITEMS_FK_NAME above) -- both confirmed against the real database,
    not guessed.

    Order matters: the child's FK depends on the parent's PK index, so
    the FK must be dropped before gamma_aggregates_pkey, not after
    (confirmed live -- the first attempt at this migration got exactly
    this DependentObjectsStillExistError from Postgres).
    """
    op.add_column(
        "gamma_aggregates",
        sa.Column("view", sa.Text(), nullable=False, server_default=sa.text("'structural'")),
    )
    op.alter_column("gamma_aggregates", "view", server_default=None)

    op.add_column(
        "gamma_aggregate_items",
        sa.Column("view", sa.Text(), nullable=False, server_default=sa.text("'structural'")),
    )
    op.alter_column("gamma_aggregate_items", "view", server_default=None)

    # Child FK and PK first -- the FK's own index depends on the parent's
    # PK, so it must go before gamma_aggregates_pkey is touched.
    op.execute(f"ALTER TABLE gamma_aggregate_items DROP CONSTRAINT {_ITEMS_FK_NAME}")
    op.execute("ALTER TABLE gamma_aggregate_items DROP CONSTRAINT gamma_aggregate_items_pkey")

    op.execute("ALTER TABLE gamma_aggregates DROP CONSTRAINT gamma_aggregates_pkey")
    op.execute(
        "ALTER TABLE gamma_aggregates "
        "ADD CONSTRAINT gamma_aggregates_pkey PRIMARY KEY (underlying_id, time, view)"
    )

    op.execute(
        "ALTER TABLE gamma_aggregate_items "
        "ADD CONSTRAINT gamma_aggregate_items_pkey PRIMARY KEY (underlying_id, time, view, strike)"
    )
    op.execute(
        "ALTER TABLE gamma_aggregate_items "
        "ADD CONSTRAINT gamma_aggregate_items_underlying_id_time_view_fkey "
        "FOREIGN KEY (underlying_id, time, view) "
        "REFERENCES gamma_aggregates (underlying_id, time, view)"
    )


def downgrade() -> None:
    """Destructive: a 2-column (underlying_id, time) PK cannot coexist
    with both a structural and a tactical row for the same moment, so
    every non-structural row is deleted first. Only run this if losing
    every persisted Tactical GammaAggregate is acceptable.

    Order matters, same reasoning as upgrade()'s own comment but working
    back the other way: gamma_aggregates' PK can't shrink to 2 columns
    while gamma_aggregate_items' 3-column FK still references it, and a
    new 2-column FK on gamma_aggregate_items can't be created until
    gamma_aggregates actually HAS a 2-column PK for it to reference (a
    3-column PK doesn't satisfy a FK declared on just 2 of those
    columns) -- so the parent's PK must shrink in the MIDDLE of this
    sequence, not at the very end.
    """
    op.execute("DELETE FROM gamma_aggregate_items WHERE view <> 'structural'")
    op.execute("DELETE FROM gamma_aggregates WHERE view <> 'structural'")

    # Drop the 3-column child FK and PK first.
    op.execute(
        "ALTER TABLE gamma_aggregate_items "
        "DROP CONSTRAINT gamma_aggregate_items_underlying_id_time_view_fkey"
    )
    op.execute("ALTER TABLE gamma_aggregate_items DROP CONSTRAINT gamma_aggregate_items_pkey")
    op.drop_column("gamma_aggregate_items", "view")

    # Now safe to shrink the parent's PK back to 2 columns.
    op.execute("ALTER TABLE gamma_aggregates DROP CONSTRAINT gamma_aggregates_pkey")
    op.drop_column("gamma_aggregates", "view")
    op.execute(
        "ALTER TABLE gamma_aggregates ADD CONSTRAINT gamma_aggregates_pkey PRIMARY KEY (underlying_id, time)"
    )

    # Only now can the child's 2-column PK/FK be recreated, referencing
    # the parent's now-2-column PK.
    op.execute(
        "ALTER TABLE gamma_aggregate_items "
        "ADD CONSTRAINT gamma_aggregate_items_pkey PRIMARY KEY (underlying_id, time, strike)"
    )
    op.execute(
        f"ALTER TABLE gamma_aggregate_items ADD CONSTRAINT {_ITEMS_FK_NAME} "
        "FOREIGN KEY (underlying_id, time) REFERENCES gamma_aggregates (underlying_id, time)"
    )
