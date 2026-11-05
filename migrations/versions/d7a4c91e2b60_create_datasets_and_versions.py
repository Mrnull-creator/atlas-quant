"""Create datasets and immutable dataset versions.

Revision ID: d7a4c91e2b60
Revises: c6e8a4b21d90
"""

from alembic import op
import sqlalchemy as sa


revision = "d7a4c91e2b60"
down_revision = "c6e8a4b21d90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "data_kind",
            sa.Enum(
                "ohlcv",
                "bid_ask",
                "raw_provider",
                name="dataset_kind_values",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "asset_class",
            sa.Enum(
                "forex",
                "equity",
                "crypto",
                "future",
                "option",
                "bond",
                "index",
                "commodity",
                "other",
                name="asset_class_values",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("instrument_symbol", sa.String(), nullable=True),
        sa.Column("provider_id", sa.String(), nullable=True),
        sa.Column(
            "timeframe",
            sa.Enum(
                "m1",
                "m5",
                "m15",
                "m30",
                "h1",
                "h4",
                "d1",
                "w1",
                "mn1",
                name="timeframe_values",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("latest_version_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
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
            name=op.f("ck_datasets_valid_dataset_identity"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["market_data_providers.provider_id"],
            name=op.f("fk_datasets_provider_id_market_data_providers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_id",
            name=op.f("pk_datasets"),
        ),
    )

    op.create_table(
        "dataset_versions",
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("storage_format", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "staging",
                "available",
                "failed",
                name="dataset_version_status_values",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="staging",
            nullable=False,
        ),
        sa.Column(
            "first_timestamp",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_timestamp",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "record_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("storage_uri", sa.String(), nullable=True),
        sa.Column("checksum_algorithm", sa.String(), nullable=True),
        sa.Column("checksum", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name=op.f("ck_dataset_versions_version_number_positive"),
        ),
        sa.CheckConstraint(
            "record_count >= 0",
            name=op.f("ck_dataset_versions_record_count_nonnegative"),
        ),
        sa.CheckConstraint(
            """
            (
                record_count = 0
                AND first_timestamp IS NULL
                AND last_timestamp IS NULL
            )
            OR
            (
                record_count > 0
                AND first_timestamp IS NOT NULL
                AND last_timestamp IS NOT NULL
                AND first_timestamp <= last_timestamp
            )
            """,
            name=op.f("ck_dataset_versions_valid_observation_boundaries"),
        ),
        sa.CheckConstraint(
            """
            (
                checksum_algorithm IS NULL
                AND checksum IS NULL
            )
            OR
            (
                checksum_algorithm IS NOT NULL
                AND checksum IS NOT NULL
            )
            """,
            name=op.f("ck_dataset_versions_checksum_metadata_pair"),
        ),
        sa.CheckConstraint(
            """
            (
                status = 'available'
                AND record_count > 0
                AND storage_uri IS NOT NULL
                AND checksum_algorithm IS NOT NULL
                AND checksum IS NOT NULL
                AND published_at IS NOT NULL
            )
            OR
            (
                status IN ('staging', 'failed')
                AND published_at IS NULL
            )
            """,
            name=op.f("ck_dataset_versions_valid_publication_state"),
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR published_at >= created_at",
            name=op.f("ck_dataset_versions_publication_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.dataset_id"],
            name=op.f("fk_dataset_versions_dataset_id_datasets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_version_id",
            name=op.f("pk_dataset_versions"),
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "version_number",
            name=op.f("uq_dataset_versions_dataset_version_number"),
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "dataset_version_id",
            name=op.f("uq_dataset_versions_dataset_version"),
        ),
    )

    # Add this after both tables exist because the relationship is cyclic.
    op.create_foreign_key(
        "fk_datasets_latest_version",
        "datasets",
        "dataset_versions",
        ["dataset_id", "latest_version_id"],
        ["dataset_id", "dataset_version_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_datasets_latest_version",
        "datasets",
        type_="foreignkey",
    )
    op.drop_table("dataset_versions")
    op.drop_table("datasets")