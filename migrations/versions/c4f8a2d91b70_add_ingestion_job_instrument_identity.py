"""add canonical instrument identity to ingestion jobs

Revision ID: c4f8a2d91b70
Revises: 5954869d1164
Create Date: 2026-11-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4f8a2d91b70"
down_revision: Union[str, Sequence[str], None] = "5954869d1164"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add an optional canonical instrument reference to ingestion jobs."""

    op.add_column(
        "ingestion_jobs",
        sa.Column("instrument_id", sa.Uuid(), nullable=True),
    )

    op.create_foreign_key(
        op.f(
            "fk_ingestion_jobs_instrument_id_instruments"
        ),
        "ingestion_jobs",
        "instruments",
        ["instrument_id"],
        ["instrument_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Remove the canonical instrument reference."""

    op.drop_constraint(
        op.f(
            "fk_ingestion_jobs_instrument_id_instruments"
        ),
        "ingestion_jobs",
        type_="foreignkey",
    )

    op.drop_column("ingestion_jobs", "instrument_id")
