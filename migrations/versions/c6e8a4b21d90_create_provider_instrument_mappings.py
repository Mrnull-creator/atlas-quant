"""create provider instrument mappings

Revision ID: c6e8a4b21d90
Revises: a42f7c91d6e3
Create Date: 2026-11-05

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6e8a4b21d90"
down_revision: Union[str, Sequence[str], None] = "a42f7c91d6e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create provider instrument mappings."""

    op.create_table(
        "provider_instrument_mappings",
        sa.Column(
            "mapping_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "instrument_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "provider_id",
            sa.String(),
            nullable=False,
        ),
        sa.Column(
            "provider_symbol",
            sa.String(),
            nullable=False,
        ),
        sa.Column(
            "provider_exchange",
            sa.String(),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.instrument_id"],
            name=op.f(
                "fk_provider_instrument_mappings_instrument_id_instruments"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["market_data_providers.provider_id"],
            name=op.f(
                "fk_provider_instrument_mappings_provider_id_market_data_providers"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "mapping_id",
            name=op.f("pk_provider_instrument_mappings"),
        ),
    )

    op.create_index(
        "uq_provider_instrument_mappings_active_provider_identifier",
        "provider_instrument_mappings",
        [
            "provider_id",
            "provider_symbol",
            "provider_exchange",
        ],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
        sqlite_where=sa.text("is_active = 1"),
    )


def downgrade() -> None:
    """Remove provider instrument mappings."""

    op.drop_index(
        "uq_provider_instrument_mappings_active_provider_identifier",
        table_name="provider_instrument_mappings",
    )
    op.drop_table("provider_instrument_mappings")