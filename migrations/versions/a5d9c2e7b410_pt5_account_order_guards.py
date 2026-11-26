"""add paper account revisions and order concurrency guards

Revision ID: a5d9c2e7b410
Revises: f7c2a91e6b43
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa


revision = "a5d9c2e7b410"
down_revision = "f7c2a91e6b43"
branch_labels = None
depends_on = None

SCHEMA = "execution"


def upgrade() -> None:
    # Fail clearly rather than silently changing existing order history.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT account_id
                    FROM execution.orders
                    WHERE status IN (
                        'pending', 'accepted', 'partially_filled'
                    )
                    GROUP BY account_id
                    HAVING COUNT(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'PT-5B migration blocked: an account has multiple active orders';
                END IF;

                IF EXISTS (
                    SELECT account_id, signal_id
                    FROM execution.orders
                    WHERE signal_id IS NOT NULL
                    GROUP BY account_id, signal_id
                    HAVING COUNT(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'PT-5B migration blocked: duplicate account signal orders exist';
                END IF;
            END
            $$;
            """
        )
    )

    op.add_column(
        "paper_accounts",
        sa.Column(
            "state_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_paper_accounts_state_revision_nonnegative",
        "paper_accounts",
        "state_revision >= 0",
        schema=SCHEMA,
    )

    op.create_index(
        "uq_execution_orders_one_active_per_account",
        "orders",
        ["account_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "status IN ('pending', 'accepted', 'partially_filled')"
        ),
    )
    op.create_index(
        "uq_execution_orders_account_signal",
        "orders",
        ["account_id", "signal_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("signal_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_execution_orders_account_signal",
        table_name="orders",
        schema=SCHEMA,
    )
    op.drop_index(
        "uq_execution_orders_one_active_per_account",
        table_name="orders",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_paper_accounts_state_revision_nonnegative",
        "paper_accounts",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column(
        "paper_accounts",
        "state_revision",
        schema=SCHEMA,
    )
