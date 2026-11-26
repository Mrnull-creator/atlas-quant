"""add durable execution attempts

Revision ID: c4e8f1a2d963
Revises: a5d9c2e7b410
Create Date: 2026-11-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c4e8f1a2d963"
down_revision = "a5d9c2e7b410"
branch_labels = None
depends_on = None

SCHEMA = "execution"


def upgrade() -> None:
    op.create_table(
        "execution_attempts",
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "fill_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="prepared",
            nullable=False,
        ),

        # Quote snapshot.
        sa.Column("quote_symbol", sa.String(length=64), nullable=False),
        sa.Column(
            "quote_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("quote_bid", sa.Numeric(38, 18), nullable=False),
        sa.Column("quote_ask", sa.Numeric(38, 18), nullable=False),
        sa.Column("quote_bid_size", sa.Numeric(38, 18), nullable=True),
        sa.Column("quote_ask_size", sa.Numeric(38, 18), nullable=True),

        # Instrument snapshot.
        sa.Column(
            "instrument_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "instrument_symbol",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("asset_class", sa.String(length=32), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=True),
        sa.Column("base_currency", sa.String(length=3), nullable=True),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("price_precision", sa.Integer(), nullable=False),
        sa.Column("quantity_precision", sa.Integer(), nullable=False),
        sa.Column("tick_size", sa.Numeric(38, 18), nullable=True),
        sa.Column("contract_size", sa.Numeric(38, 18), nullable=True),
        sa.Column(
            "instrument_is_active",
            sa.Boolean(),
            nullable=False,
        ),

        # Simulation configuration.
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "commission_per_1000_units",
            sa.Numeric(38, 18),
            nullable=False,
        ),
        sa.Column("slippage_ticks", sa.Integer(), nullable=False),
        sa.Column(
            "max_quote_age_microseconds",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "missing_liquidity_policy",
            sa.String(length=32),
            nullable=False,
        ),

        # Result.
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reference_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("execution_price", sa.Numeric(38, 18), nullable=True),
        sa.Column(
            "slippage_cost",
            sa.Numeric(38, 18),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "liquidity_assumed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),

        sa.PrimaryKeyConstraint(
            "attempt_id",
            name="pk_execution_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "order_id"],
            [
                "execution.orders.account_id",
                "execution.orders.order_id",
            ],
            ondelete="RESTRICT",
            name="fk_execution_attempts_account_order",
        ),
        sa.UniqueConstraint(
            "order_id",
            "attempt_number",
            name="uq_execution_attempts_order_attempt_number",
        ),
        sa.UniqueConstraint(
            "fill_id",
            name="uq_execution_attempts_fill_id",
        ),

        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_execution_attempts_attempt_number_positive",
        ),
        sa.CheckConstraint(
            "status IN ('prepared', 'completed', 'failed')",
            name="ck_execution_attempts_status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'prepared' AND outcome IS NULL "
            "AND completed_at IS NULL) OR "
            "(status = 'completed' AND outcome IN "
            "('filled', 'partially_filled', 'no_liquidity', 'rejected') "
            "AND completed_at IS NOT NULL) OR "
            "(status = 'failed' AND outcome IS NULL "
            "AND completed_at IS NOT NULL AND reason IS NOT NULL)",
            name="ck_execution_attempts_lifecycle_consistency",
        ),
        sa.CheckConstraint(
            "quote_bid > 0 AND quote_ask > 0",
            name="ck_execution_attempts_quote_prices_positive",
        ),
        sa.CheckConstraint(
            "(quote_bid_size IS NULL OR quote_bid_size >= 0) AND "
            "(quote_ask_size IS NULL OR quote_ask_size >= 0)",
            name="ck_execution_attempts_quote_sizes_nonnegative",
        ),
        sa.CheckConstraint(
            "commission_per_1000_units >= 0",
            name="ck_execution_attempts_commission_nonnegative",
        ),
        sa.CheckConstraint(
            "slippage_ticks >= 0",
            name="ck_execution_attempts_slippage_ticks_nonnegative",
        ),
        sa.CheckConstraint(
            "max_quote_age_microseconds IS NULL OR "
            "max_quote_age_microseconds > 0",
            name="ck_execution_attempts_max_quote_age_positive",
        ),
        sa.CheckConstraint(
            "missing_liquidity_policy IN ('reject', 'assume_full_fill')",
            name="ck_execution_attempts_liquidity_policy_valid",
        ),
        sa.CheckConstraint(
            "price_precision >= 0 AND quantity_precision >= 0",
            name="ck_execution_attempts_precision_nonnegative",
        ),
        sa.CheckConstraint(
            "tick_size IS NULL OR tick_size > 0",
            name="ck_execution_attempts_tick_size_positive",
        ),
        sa.CheckConstraint(
            "contract_size IS NULL OR contract_size > 0",
            name="ck_execution_attempts_contract_size_positive",
        ),
        sa.CheckConstraint(
            "length(quote_currency) = 3 AND "
            "quote_currency = upper(quote_currency)",
            name="ck_execution_attempts_quote_currency_format",
        ),
        sa.CheckConstraint(
            "base_currency IS NULL OR "
            "(length(base_currency) = 3 AND "
            "base_currency = upper(base_currency))",
            name="ck_execution_attempts_base_currency_format",
        ),
        sa.CheckConstraint(
            "reference_price IS NULL OR reference_price > 0",
            name="ck_execution_attempts_reference_price_positive",
        ),
        sa.CheckConstraint(
            "execution_price IS NULL OR execution_price > 0",
            name="ck_execution_attempts_execution_price_positive",
        ),
        sa.CheckConstraint(
            "slippage_cost >= 0",
            name="ck_execution_attempts_slippage_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="ck_execution_attempts_timestamps_ordered",
        ),
        schema=SCHEMA,
    )

    op.create_index(
        "uq_execution_attempts_one_prepared_per_order",
        "execution_attempts",
        ["order_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'prepared'"),
    )

    op.create_index(
        "ix_execution_attempts_order_created_at",
        "execution_attempts",
        ["order_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )

    # Prevent mutation of the original attempt identity and input snapshot.
    op.execute(
        sa.text(
            """
            CREATE FUNCTION execution.prevent_execution_attempt_input_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF ROW(
                    NEW.account_id,
                    NEW.order_id,
                    NEW.attempt_number,
                    NEW.fill_id,
                    NEW.quote_symbol,
                    NEW.quote_timestamp,
                    NEW.quote_bid,
                    NEW.quote_ask,
                    NEW.quote_bid_size,
                    NEW.quote_ask_size,
                    NEW.instrument_id,
                    NEW.instrument_symbol,
                    NEW.asset_class,
                    NEW.exchange,
                    NEW.base_currency,
                    NEW.quote_currency,
                    NEW.price_precision,
                    NEW.quantity_precision,
                    NEW.tick_size,
                    NEW.contract_size,
                    NEW.instrument_is_active,
                    NEW.executed_at,
                    NEW.commission_per_1000_units,
                    NEW.slippage_ticks,
                    NEW.max_quote_age_microseconds,
                    NEW.missing_liquidity_policy,
                    NEW.created_at
                ) IS DISTINCT FROM ROW(
                    OLD.account_id,
                    OLD.order_id,
                    OLD.attempt_number,
                    OLD.fill_id,
                    OLD.quote_symbol,
                    OLD.quote_timestamp,
                    OLD.quote_bid,
                    OLD.quote_ask,
                    OLD.quote_bid_size,
                    OLD.quote_ask_size,
                    OLD.instrument_id,
                    OLD.instrument_symbol,
                    OLD.asset_class,
                    OLD.exchange,
                    OLD.base_currency,
                    OLD.quote_currency,
                    OLD.price_precision,
                    OLD.quantity_precision,
                    OLD.tick_size,
                    OLD.contract_size,
                    OLD.instrument_is_active,
                    OLD.executed_at,
                    OLD.commission_per_1000_units,
                    OLD.slippage_ticks,
                    OLD.max_quote_age_microseconds,
                    OLD.missing_liquidity_policy,
                    OLD.created_at
                ) THEN
                    RAISE EXCEPTION
                        'Execution attempt inputs are immutable';
                END IF;

                RETURN NEW;
            END;
            $$;
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_execution_attempt_inputs_immutable
            BEFORE UPDATE ON execution.execution_attempts
            FOR EACH ROW
            EXECUTE FUNCTION
                execution.prevent_execution_attempt_input_mutation();
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DROP TRIGGER IF EXISTS
                trg_execution_attempt_inputs_immutable
            ON execution.execution_attempts;
            """
        )
    )

    op.execute(
        sa.text(
            """
            DROP FUNCTION IF EXISTS
                execution.prevent_execution_attempt_input_mutation();
            """
        )
    )

    op.drop_index(
        "ix_execution_attempts_order_created_at",
        table_name="execution_attempts",
        schema=SCHEMA,
    )

    op.drop_index(
        "uq_execution_attempts_one_prepared_per_order",
        table_name="execution_attempts",
        schema=SCHEMA,
    )

    op.drop_table(
        "execution_attempts",
        schema=SCHEMA,
    )