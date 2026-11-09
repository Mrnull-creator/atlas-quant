"""Support opaque raw-provider dataset versions and storage sizes.

Revision ID: f2a91c6d8e40
Revises: c4f8a2d91b70
"""

from alembic import op
import sqlalchemy as sa


revision = "f2a91c6d8e40"
down_revision = "c4f8a2d91b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow opaque raw versions and persist physical object sizes."""

    op.add_column(
        "dataset_versions",
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
    )

    # These are the names created by the earlier migrations.
    op.drop_constraint(
        op.f("ck_dataset_versions_record_count_nonnegative"),
        "dataset_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_dataset_versions_valid_observation_boundaries"),
        "dataset_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_dataset_versions_valid_publication_state"),
        "dataset_versions",
        type_="check",
    )

    op.alter_column(
        "dataset_versions",
        "record_count",
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    # These names match the current ORM naming convention.
    op.create_check_constraint(
        op.f("ck_dataset_versions_ck_record_count_nonnegative"),
        "dataset_versions",
        "record_count IS NULL OR record_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_dataset_versions_ck_size_bytes_nonnegative"),
        "dataset_versions",
        "size_bytes IS NULL OR size_bytes >= 0",
    )
    op.create_check_constraint(
        op.f("ck_dataset_versions_ck_valid_observation_boundaries"),
        "dataset_versions",
        """
        (
            record_count IS NULL
            AND first_timestamp IS NULL
            AND last_timestamp IS NULL
        )
        OR
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
    )
    op.create_check_constraint(
        op.f("ck_dataset_versions_ck_valid_publication_state"),
        "dataset_versions",
        """
        (
            status = 'available'
            AND (record_count IS NULL OR record_count > 0)
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
    )


def downgrade() -> None:
    """Restore the original normalized-only version constraints."""

    connection = op.get_bind()

    raw_versions = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM dataset_versions
            WHERE record_count IS NULL
            """
        )
    ).scalar_one()

    if raw_versions:
        raise RuntimeError(
            "Cannot downgrade while dataset versions have NULL "
            "record_count values. Those values represent opaque raw "
            "provider objects and cannot safely be converted to "
            "normalized observation counts."
        )

    # Remove the new ORM-convention names.
    op.drop_constraint(
        op.f("ck_dataset_versions_ck_record_count_nonnegative"),
        "dataset_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_dataset_versions_ck_size_bytes_nonnegative"),
        "dataset_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_dataset_versions_ck_valid_observation_boundaries"),
        "dataset_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_dataset_versions_ck_valid_publication_state"),
        "dataset_versions",
        type_="check",
    )

    op.alter_column(
        "dataset_versions",
        "record_count",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    # Restore the original historical constraint names.
    op.create_check_constraint(
        op.f("ck_dataset_versions_record_count_nonnegative"),
        "dataset_versions",
        "record_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_dataset_versions_valid_observation_boundaries"),
        "dataset_versions",
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
    )
    op.create_check_constraint(
        op.f("ck_dataset_versions_valid_publication_state"),
        "dataset_versions",
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
    )

    op.drop_column("dataset_versions", "size_bytes")