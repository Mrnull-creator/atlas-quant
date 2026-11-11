"""Add request identity to ingestion jobs.

Revision ID: a1b2c3d4e5f6
Revises: d3e7a9c2f641
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "d3e7a9c2f641"
branch_labels = None
depends_on = None


_REQUEST_IDENTITY_CONSTRAINT = """
(
    data_kind IS NULL
    AND provider_mapping_id IS NULL
)
OR
(
    data_kind IS NOT NULL
    AND data_kind IN ('ohlcv', 'bid_ask')
    AND provider_mapping_id IS NOT NULL
    AND instrument_id IS NOT NULL
    AND (
        (
            data_kind = 'ohlcv'
            AND timeframe IS NOT NULL
        )
        OR
        (
            data_kind = 'bid_ask'
            AND timeframe IS NULL
        )
    )
)
"""


def upgrade() -> None:
    """Add optional request identity fields and enforce their consistency."""

    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "data_kind",
            sa.String(length=12),
            nullable=True,
        ),
    )

    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "provider_mapping_id",
            sa.Uuid(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_ingestion_jobs_provider_mapping_id",
        "ingestion_jobs",
        "provider_instrument_mappings",
        ["provider_mapping_id"],
        ["mapping_id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_ingestion_jobs_valid_request_identity",
        "ingestion_jobs",
        _REQUEST_IDENTITY_CONSTRAINT,
    )


def downgrade() -> None:
    """Remove request identity fields without silently losing stored data."""

    connection = op.get_bind()

    has_identity_data = connection.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM ingestion_jobs
                WHERE data_kind IS NOT NULL
                   OR provider_mapping_id IS NOT NULL
            )
            """
        )
    ).scalar_one()

    if has_identity_data:
        raise RuntimeError(
            "Cannot downgrade ingestion job request identity while "
            "ingestion jobs contain data_kind or provider_mapping_id. "
            "Preserve or migrate this data before downgrading."
        )

    op.drop_constraint(
        "ck_ingestion_jobs_valid_request_identity",
        "ingestion_jobs",
        type_="check",
    )

    op.drop_constraint(
        "fk_ingestion_jobs_provider_mapping_id",
        "ingestion_jobs",
        type_="foreignkey",
    )

    op.drop_column("ingestion_jobs", "provider_mapping_id")
    op.drop_column("ingestion_jobs", "data_kind")