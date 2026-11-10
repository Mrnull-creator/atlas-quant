"""Add canonical instrument identity to normalized datasets.

Revision ID: d3e7a9c2f641
Revises: b7c3e9a1d642
"""

from alembic import op
import sqlalchemy as sa


revision = "d3e7a9c2f641"
down_revision = "b7c3e9a1d642"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing normalized datasets were verified to be absent.
    # Keep this nullable because raw-provider datasets may not
    # have a canonical instrument identity.
    op.add_column(
        "datasets",
        sa.Column("instrument_id", sa.Uuid(), nullable=True),
    )

    # Candidate key required by the composite dataset reference.
    op.create_unique_constraint(
        "uq_instruments_identity_reference",
        "instruments",
        ["instrument_id", "symbol", "asset_class"],
    )

    # Direct reference also supports raw-provider datasets that
    # have an instrument_id but no normalized symbol identity.
    op.create_foreign_key(
        op.f("fk_datasets_instrument_id_instruments"),
        "datasets",
        "instruments",
        ["instrument_id"],
        ["instrument_id"],
        ondelete="RESTRICT",
    )

    # Ensure normalized dataset descriptive fields agree with
    # the referenced canonical instrument.
    op.create_foreign_key(
        "fk_datasets_instrument_identity",
        "datasets",
        "instruments",
        ["instrument_id", "instrument_symbol", "asset_class"],
        ["instrument_id", "symbol", "asset_class"],
        ondelete="RESTRICT",
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
            data_kind = 'ohlcv'
            AND instrument_id IS NOT NULL
            AND instrument_symbol IS NOT NULL
            AND timeframe IS NOT NULL
        )
        OR
        (
            data_kind = 'bid_ask'
            AND instrument_id IS NOT NULL
            AND instrument_symbol IS NOT NULL
            AND timeframe IS NULL
        )
        """,
    )

    # One normalized OHLCV dataset per instrument and timeframe.
    op.create_index(
        "uq_datasets_ohlcv_instrument_timeframe",
        "datasets",
        ["instrument_id", "timeframe"],
        unique=True,
        postgresql_where=sa.text("data_kind = 'ohlcv'"),
    )

    # One normalized bid/ask dataset per instrument.
    op.create_index(
        "uq_datasets_bid_ask_instrument",
        "datasets",
        ["instrument_id"],
        unique=True,
        postgresql_where=sa.text("data_kind = 'bid_ask'"),
    )


def downgrade() -> None:
    # Never silently discard canonical identities that may have
    # been assigned after this migration.
    connection = op.get_bind()
    normalized_count = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM datasets
            WHERE data_kind IN ('ohlcv', 'bid_ask')
            """
        )
    )

    if normalized_count:
        raise RuntimeError(
            "Cannot downgrade canonical dataset identity while "
            "normalized datasets exist. Their instrument identities "
            "would be lost."
        )

    op.drop_index(
        "uq_datasets_bid_ask_instrument",
        table_name="datasets",
    )
    op.drop_index(
        "uq_datasets_ohlcv_instrument_timeframe",
        table_name="datasets",
    )

    op.drop_constraint(
        "ck_datasets_valid_dataset_identity",
        "datasets",
        type_="check",
    )

    op.drop_constraint(
        "fk_datasets_instrument_identity",
        "datasets",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_datasets_instrument_id_instruments"),
        "datasets",
        type_="foreignkey",
    )

    op.drop_constraint(
        "uq_instruments_identity_reference",
        "instruments",
        type_="unique",
    )

    op.drop_column("datasets", "instrument_id")

    # Restore the previous dataset identity rules.
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
