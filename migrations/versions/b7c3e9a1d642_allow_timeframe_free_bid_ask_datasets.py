"""Allow bid/ask datasets without a bar timeframe.

Revision ID: b7c3e9a1d642
Revises: f2a91c6d8e40
"""

import sqlalchemy as sa
from alembic import op


revision = "b7c3e9a1d642"
down_revision = "f2a91c6d8e40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Require timeframes only for OHLCV datasets."""

    op.drop_constraint(
        op.f("ck_datasets_valid_dataset_identity"),
        "datasets",
        type_="check",
    )

    op.create_check_constraint(
        op.f("ck_datasets_valid_dataset_identity"),
        "datasets",
        """
        (
            data_kind = 'raw_provider'
            AND provider_id IS NOT NULL
        )
        OR
        (
            data_kind = 'ohlcv'
            AND instrument_symbol IS NOT NULL
            AND timeframe IS NOT NULL
        )
        OR
        (
            data_kind = 'bid_ask'
            AND instrument_symbol IS NOT NULL
            AND timeframe IS NULL
        )
        """,
    )


def downgrade() -> None:
    """Restore the original normalized-dataset timeframe requirement."""

    connection = op.get_bind()

    incompatible_datasets = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM datasets
            WHERE data_kind = 'bid_ask'
              AND timeframe IS NULL
            """
        )
    ).scalar_one()

    if incompatible_datasets:
        raise RuntimeError(
            "Cannot downgrade while bid/ask datasets without a "
            "timeframe exist. Assign valid timeframes or remove those "
            "datasets before downgrading."
        )

    op.drop_constraint(
        op.f("ck_datasets_valid_dataset_identity"),
        "datasets",
        type_="check",
    )

    op.create_check_constraint(
        op.f("ck_datasets_valid_dataset_identity"),
        "datasets",
        """
        (
            data_kind = 'raw_provider'
            AND provider_id IS NOT NULL
        )
        OR
        (
            data_kind IN ('ohlcv', 'bid_ask')
            AND instrument_symbol IS NOT NULL
            AND timeframe IS NOT NULL
        )
        """,
    )
