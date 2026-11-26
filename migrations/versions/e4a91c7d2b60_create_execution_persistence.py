"""create paper trading execution persistence

Revision ID: e4a91c7d2b60
Revises: 9c31a7e4b620
Create Date: 2026-11-22
"""

from alembic import op
import sqlalchemy as sa


revision = "e4a91c7d2b60"
down_revision = "9c31a7e4b620"
branch_labels = None
depends_on = None


SCHEMA = "execution"


def upgrade() -> None:
    """Create the Execution Service's independently owned persistence schema."""

    op.execute(sa.text("CREATE SCHEMA execution"))

    op.create_table(
        "paper_accounts",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("initial_capital", sa.Numeric(38, 18), nullable=False),
        sa.Column("current_cash", sa.Numeric(38, 18), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(base_currency) = 3 AND base_currency = upper(base_currency)",
            name="ck_paper_accounts_base_currency_format",
        ),
        sa.CheckConstraint(
            "initial_capital > 0",
            name="ck_paper_accounts_initial_capital_positive",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_paper_accounts_status_valid",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_paper_accounts_timestamps_ordered",
        ),
        sa.PrimaryKeyConstraint("account_id", name="pk_paper_accounts"),
        schema=SCHEMA,
    )

    op.create_table(
        "orders",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column(
            "order_type",
            sa.String(length=12),
            server_default="market",
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "filled_quantity",
            sa.Numeric(38, 18),
            server_default="0",
            nullable=False,
        ),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("signal_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "side IN ('buy', 'sell')",
            name="ck_orders_side_valid",
        ),
        sa.CheckConstraint(
            "order_type = 'market'",
            name="ck_orders_order_type_valid",
        ),
        sa.CheckConstraint(
            """
            status IN (
                'pending',
                'accepted',
                'partially_filled',
                'filled',
                'cancelled',
                'rejected'
            )
            """,
            name="ck_orders_status_valid",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_orders_quantity_positive",
        ),
        sa.CheckConstraint(
            "filled_quantity >= 0 AND filled_quantity <= quantity",
            name="ck_orders_filled_quantity_range",
        ),
        sa.CheckConstraint(
            """
            (
                status = 'filled'
                AND filled_quantity = quantity
            )
            OR (
                status = 'partially_filled'
                AND filled_quantity > 0
                AND filled_quantity < quantity
            )
            OR (
                status IN ('pending', 'accepted', 'rejected')
                AND filled_quantity = 0
            )
            OR status = 'cancelled'
            """,
            name="ck_orders_status_fill_consistency",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_orders_timestamps_ordered",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["execution.paper_accounts.account_id"],
            name="fk_orders_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("order_id", name="pk_orders"),
        sa.UniqueConstraint(
            "account_id",
            "order_id",
            name="uq_execution_orders_account_order",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "fills",
        sa.Column("fill_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("commission", sa.Numeric(38, 18), nullable=False),
        sa.Column("commission_currency", sa.String(length=3), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_fills_quantity_positive",
        ),
        sa.CheckConstraint(
            "price > 0",
            name="ck_fills_price_positive",
        ),
        sa.CheckConstraint(
            "commission >= 0",
            name="ck_fills_commission_nonnegative",
        ),
        sa.CheckConstraint(
            """
            length(commission_currency) = 3
            AND commission_currency = upper(commission_currency)
            """,
            name="ck_fills_commission_currency_format",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "order_id"],
            ["execution.orders.account_id", "execution.orders.order_id"],
            name="fk_fills_account_order",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("fill_id", name="pk_fills"),
        schema=SCHEMA,
    )

    op.create_index(
        "ix_execution_fills_account_executed_at",
        "fills",
        ["account_id", "executed_at"],
        schema=SCHEMA,
    )

    op.create_index(
        "ix_execution_fills_order_executed_at",
        "fills",
        ["order_id", "executed_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "cash_ledger",
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("entry_type", sa.String(length=12), nullable=False),
        sa.Column("amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("reference_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entry_type IN ('trade', 'commission', 'adjustment')",
            name="ck_cash_ledger_entry_type_valid",
        ),
        sa.CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_cash_ledger_currency_format",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["execution.paper_accounts.account_id"],
            name="fk_cash_ledger_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("entry_id", name="pk_cash_ledger"),
        schema=SCHEMA,
    )

    op.create_index(
        "ix_execution_cash_ledger_account_occurred_at",
        "cash_ledger",
        ["account_id", "occurred_at"],
        schema=SCHEMA,
    )

    op.create_index(
        "uq_execution_cash_ledger_trade_reference",
        "cash_ledger",
        ["account_id", "reference_id"],
        unique=True,
        postgresql_where=sa.text("entry_type = 'trade'"),
        schema=SCHEMA,
    )

    op.create_index(
        "uq_execution_cash_ledger_commission_reference",
        "cash_ledger",
        ["account_id", "reference_id"],
        unique=True,
        postgresql_where=sa.text("entry_type = 'commission'"),
        schema=SCHEMA,
    )

    op.create_table(
        "positions",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("average_entry_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(38, 18), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("contract_size", sa.Numeric(38, 18), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            """
            (quantity = 0 AND average_entry_price = 0)
            OR
            (quantity <> 0 AND average_entry_price > 0)
            """,
            name="ck_positions_quantity_average_price_consistency",
        ),
        sa.CheckConstraint(
            "contract_size > 0",
            name="ck_positions_contract_size_positive",
        ),
        sa.CheckConstraint(
            "length(quote_currency) = 3 AND quote_currency = upper(quote_currency)",
            name="ck_positions_quote_currency_format",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["execution.paper_accounts.account_id"],
            name="fk_positions_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "account_id",
            "instrument_id",
            name="pk_positions",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Refuse to drop execution persistence while it contains user data."""

    bind = op.get_bind()

    tables = (
        "paper_accounts",
        "orders",
        "fills",
        "cash_ledger",
        "positions",
    )

    for table_name in tables:
        has_data = bind.execute(
            sa.text(
                f'SELECT EXISTS (SELECT 1 FROM execution."{table_name}" LIMIT 1)'
            )
        ).scalar_one()

        if has_data:
            raise RuntimeError(
                "Cannot downgrade Execution Service persistence: "
                f"execution.{table_name} contains data. "
                "Export or explicitly preserve that data before proceeding."
            )

    op.drop_table("positions", schema=SCHEMA)
    op.drop_table("cash_ledger", schema=SCHEMA)
    op.drop_table("fills", schema=SCHEMA)
    op.drop_table("orders", schema=SCHEMA)
    op.drop_table("paper_accounts", schema=SCHEMA)

    op.execute(sa.text("DROP SCHEMA execution"))
