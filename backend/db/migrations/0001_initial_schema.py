"""Initial QLL schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the documented PostgreSQL + TimescaleDB schema."""
    # Temporary compatibility path: local development may use plain PostgreSQL.
    # Once TimescaleDB is installed, a later migration can convert these regular
    # tables to hypertables without changing their logical schema.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'
            ) THEN
                CREATE EXTENSION IF NOT EXISTS timescaledb;
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS underlyings (
            id serial PRIMARY KEY,
            symbol text NOT NULL UNIQUE,
            kind text NOT NULL CHECK (kind IN ('equity', 'index')),
            is_priority boolean NOT NULL DEFAULT false
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS option_contracts (
            id serial PRIMARY KEY,
            underlying_id integer NOT NULL REFERENCES underlyings(id),
            strike numeric NOT NULL,
            expiration date NOT NULL,
            contract_type text NOT NULL CHECK (contract_type IN ('call', 'put')),
            occ_symbol text NOT NULL UNIQUE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS option_chain_snapshots (
            time timestamptz NOT NULL,
            contract_id integer NOT NULL REFERENCES option_contracts(id),
            bid numeric NOT NULL,
            ask numeric NOT NULL,
            last numeric NOT NULL,
            volume integer NOT NULL,
            open_interest integer NOT NULL,
            iv numeric NOT NULL,
            delta numeric NOT NULL,
            gamma numeric NOT NULL,
            theta numeric NULL,
            vega numeric NULL
        )
        """
    )
    _create_hypertable_if_available("option_chain_snapshots")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_option_chain_snapshots_contract_time
        ON option_chain_snapshots (contract_id, time DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gamma_aggregates (
            time timestamptz NOT NULL,
            underlying_id integer NOT NULL REFERENCES underlyings(id),
            gamma_flip numeric NOT NULL,
            call_wall numeric NOT NULL,
            put_wall numeric NOT NULL,
            max_pain numeric NOT NULL,
            net_gamma numeric NOT NULL,
            dealer_gamma_notional numeric NOT NULL
        )
        """
    )
    _create_hypertable_if_available("gamma_aggregates")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gamma_aggregates_underlying_time
        ON gamma_aggregates (underlying_id, time DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS market_snapshots (
            time timestamptz NOT NULL,
            underlying_id integer NOT NULL REFERENCES underlyings(id),
            price numeric NOT NULL,
            volume bigint NOT NULL
        )
        """
    )
    _create_hypertable_if_available("market_snapshots")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS flow_events (
            time timestamptz NOT NULL,
            contract_id integer NOT NULL REFERENCES option_contracts(id),
            event_type text NOT NULL CHECK (event_type IN ('sweep', 'block', 'unusual')),
            premium numeric NOT NULL,
            size integer NOT NULL,
            aggressor_side text NOT NULL CHECK (aggressor_side IN ('buy', 'sell', 'unknown'))
        )
        """
    )
    _create_hypertable_if_available("flow_events")


def _create_hypertable_if_available(table_name: str) -> None:
    """Convert a table only when TimescaleDB is installed in this database."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'
            ) THEN
                PERFORM create_hypertable(
                    '{table_name}', 'time', if_not_exists => TRUE
                );
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    """Drop schema objects in reverse dependency order."""
    op.execute("DROP TABLE IF EXISTS flow_events")
    op.execute("DROP TABLE IF EXISTS market_snapshots")
    op.execute("DROP TABLE IF EXISTS gamma_aggregates")
    op.execute("DROP TABLE IF EXISTS option_chain_snapshots")
    op.execute("DROP TABLE IF EXISTS option_contracts")
    op.execute("DROP TABLE IF EXISTS underlyings")
