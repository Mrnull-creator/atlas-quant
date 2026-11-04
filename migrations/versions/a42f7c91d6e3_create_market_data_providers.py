"""create market data providers

Revision ID: a42f7c91d6e3
Revises: 9d0b140f0cff
Create Date: 2026-11-04

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a42f7c91d6e3"
down_revision: Union[str, Sequence[str], None] = "9d0b140f0cff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the market data providers table."""

    op.create_table(
        "market_data_providers",
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "supported_asset_classes",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "supported_timeframes",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "supports_historical_data",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "supports_quote_data",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "supports_historical_data OR supports_quote_data",
            name=op.f(
                "ck_market_data_providers_at_least_one_data_capability"
            ),
        ),
        sa.PrimaryKeyConstraint(
            "provider_id",
            name=op.f("pk_market_data_providers"),
        ),
    )


def downgrade() -> None:
    """Remove the market data providers table."""

    op.drop_table("market_data_providers")
