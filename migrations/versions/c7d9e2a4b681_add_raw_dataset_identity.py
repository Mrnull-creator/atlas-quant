"""Add atomic identity for raw provider datasets.

Revision ID: c7d9e2a4b681
Revises: a1b2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa


revision = "c7d9e2a4b681"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_datasets_raw_provider_instrument"


def upgrade() -> None:
    connection = op.get_bind()

    duplicate = connection.execute(
        sa.text(
            """
            SELECT provider_id, instrument_id, COUNT(*)
            FROM datasets
            WHERE data_kind = 'raw_provider'
              AND instrument_id IS NOT NULL
            GROUP BY provider_id, instrument_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()

    if duplicate is not None:
        raise RuntimeError(
            "Cannot create raw dataset identity index because duplicate "
            "raw provider datasets already exist for provider "
            f"{duplicate[0]!r} and instrument {duplicate[1]!r}. "
            "Resolve the duplicates before retrying the migration."
        )

    op.create_index(
        INDEX_NAME,
        "datasets",
        ["provider_id", "instrument_id"],
        unique=True,
        postgresql_where=sa.text(
            "data_kind = 'raw_provider' "
            "AND instrument_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        INDEX_NAME,
        table_name="datasets",
    )