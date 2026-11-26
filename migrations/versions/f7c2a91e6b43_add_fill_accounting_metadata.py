"""add complete accounting metadata to execution fills

Revision ID: f7c2a91e6b43
Revises: e4a91c7d2b60
Create Date: 2026-11-22
"""

from alembic import op
import sqlalchemy as sa


revision = "f7c2a91e6b43"
down_revision = "e4a91c7d2b60"
branch_labels = None
depends_on = None


SCHEMA = "execution"
TABLE = "fills"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("contract_size", sa.Numeric(38, 18), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("quote_currency", sa.String(3), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column(
            "accounting_metadata_version",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
        schema=SCHEMA,
    )

    # Legacy rows cannot reveal their original multiplier. Preserve an
    # explicit legacy marker instead of pretending the default is verified.
    op.execute(
        sa.text(
            """
            UPDATE execution.fills
            SET contract_size = 1,
                quote_currency = commission_currency,
                accounting_metadata_version = 0
            WHERE contract_size IS NULL
               OR quote_currency IS NULL
            """
        )
    )

    op.alter_column(
        TABLE,
        "contract_size",
        existing_type=sa.Numeric(38, 18),
        nullable=False,
        schema=SCHEMA,
    )
    op.alter_column(
        TABLE,
        "quote_currency",
        existing_type=sa.String(3),
        nullable=False,
        schema=SCHEMA,
    )
    op.alter_column(
        TABLE,
        "accounting_metadata_version",
        existing_type=sa.SmallInteger(),
        server_default="1",
        existing_nullable=False,
        schema=SCHEMA,
    )

    op.create_check_constraint(
        "ck_fills_contract_size_positive",
        TABLE,
        "contract_size > 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_fills_quote_currency_format",
        TABLE,
        """
        length(quote_currency) = 3
        AND quote_currency = upper(quote_currency)
        """,
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_fills_accounting_metadata_version_valid",
        TABLE,
        "accounting_metadata_version IN (0, 1)",
        schema=SCHEMA,
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_data = bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM execution.fills LIMIT 1)")
    ).scalar_one()

    if has_data:
        raise RuntimeError(
            "Cannot downgrade fill accounting metadata while execution.fills "
            "contains data; dropping these columns would destroy accounting "
            "history."
        )

    op.drop_constraint(
        "ck_fills_accounting_metadata_version_valid",
        TABLE,
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_fills_quote_currency_format",
        TABLE,
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_fills_contract_size_positive",
        TABLE,
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column(TABLE, "accounting_metadata_version", schema=SCHEMA)
    op.drop_column(TABLE, "quote_currency", schema=SCHEMA)
    op.drop_column(TABLE, "contract_size", schema=SCHEMA)
