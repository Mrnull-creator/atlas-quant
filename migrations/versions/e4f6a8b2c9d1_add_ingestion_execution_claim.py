"""Add ingestion job execution claim.

Revision ID: e4f6a8b2c9d1
Revises: c7d9e2a4b681
"""

from alembic import op
import sqlalchemy as sa


revision = "e4f6a8b2c9d1"
down_revision = "c7d9e2a4b681"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "execution_claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "ingestion_jobs",
        "execution_claimed_at",
    )
