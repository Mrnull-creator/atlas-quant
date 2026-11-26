"""add ingestion attempt recovery model

Revision ID: 7521178cd761
Revises: e4f6a8b2c9d1
Create Date: 2026-11-17
"""

from alembic import op
import sqlalchemy as sa


revision = "7521178cd761"
down_revision = "e4f6a8b2c9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the ingestion attempt recovery model."""

    # 1. Create durable ingestion attempts.
    op.create_table(
        "ingestion_attempts",
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.BigInteger(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=13),
            server_default="running",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("failure_message", sa.String(), nullable=True),

        sa.PrimaryKeyConstraint(
            "attempt_id",
            name=op.f("pk_ingestion_attempts"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["ingestion_jobs.job_id"],
            name=op.f("fk_ingestion_attempts_job_id_ingestion_jobs"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "job_id",
            "attempt_id",
            name=op.f("uq_ingestion_attempts_job_attempt"),
        ),
        sa.UniqueConstraint(
            "job_id",
            "attempt_number",
            name=op.f("uq_ingestion_attempts_job_attempt_number"),
        ),
        sa.UniqueConstraint(
            "job_id",
            "fencing_token",
            name=op.f("uq_ingestion_attempts_job_fencing_token"),
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name=op.f("ck_ingestion_attempts_attempt_number_positive"),
        ),
        sa.CheckConstraint(
            "fencing_token >= 1",
            name=op.f("ck_ingestion_attempts_fencing_token_positive"),
        ),
        sa.CheckConstraint(
            "length(trim(worker_id)) > 0",
            name=op.f("ck_ingestion_attempts_worker_id_nonempty"),
        ),
        sa.CheckConstraint(
            "heartbeat_at >= started_at",
            name=op.f("ck_ingestion_attempts_heartbeat_after_start"),
        ),
        sa.CheckConstraint(
            "lease_expires_at > heartbeat_at",
            name=op.f("ck_ingestion_attempts_lease_after_heartbeat"),
        ),
        sa.CheckConstraint(
            """
            finished_at IS NULL
            OR finished_at >= started_at
            """,
            name=op.f("ck_ingestion_attempts_finish_after_start"),
        ),
        sa.CheckConstraint(
            """
            finished_at IS NULL
            OR finished_at >= heartbeat_at
            """,
            name=op.f("ck_ingestion_attempts_finish_after_heartbeat"),
        ),
        sa.CheckConstraint(
            """
            (
                status = 'running'
                AND finished_at IS NULL
                AND failure_code IS NULL
                AND failure_message IS NULL
            )
            OR
            (
                status = 'succeeded'
                AND finished_at IS NOT NULL
                AND failure_code IS NULL
                AND failure_message IS NULL
            )
            OR
            (
                status = 'failed'
                AND finished_at IS NOT NULL
                AND failure_message IS NOT NULL
            )
            OR
            (
                status IN ('lease_expired', 'superseded')
                AND finished_at IS NOT NULL
            )
            """,
            name=op.f("ck_ingestion_attempts_valid_attempt_lifecycle"),
        ),
        sa.CheckConstraint(
            """
            status IN (
                'running',
                'succeeded',
                'failed',
                'lease_expired',
                'superseded'
            )
            """,
            name=op.f(
                "ck_ingestion_attempts_ingestion_attempt_status_values"
            ),
        ),
    )

    # 2. Index attempts by job and status.
    op.create_index(
        op.f("ix_ingestion_attempts_job_id_status"),
        "ingestion_attempts",
        ["job_id", "status"],
        unique=False,
    )

    # 3. Add current attempt reference to ingestion jobs.
    op.add_column(
        "ingestion_jobs",
        sa.Column("current_attempt_id", sa.Uuid(), nullable=True),
    )

    # 4. Add verification and attempt lineage to dataset versions.
    op.add_column(
        "dataset_versions",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "dataset_versions",
        sa.Column("ingestion_attempt_id", sa.Uuid(), nullable=True),
    )

    op.create_foreign_key(
        op.f(
            "fk_dataset_versions_ingestion_attempt_id_ingestion_attempts"
        ),
        "dataset_versions",
        "ingestion_attempts",
        ["ingestion_attempt_id"],
        ["attempt_id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_ingestion_jobs_current_attempt",
        "ingestion_jobs",
        "ingestion_attempts",
        ["job_id", "current_attempt_id"],
        ["job_id", "attempt_id"],
        ondelete="RESTRICT",
    )

    # 5. Drop the existing lifecycle constraints.
    #
    # op.f() prevents Alembic from applying the naming convention
    # a second time. These are the exact existing database names.
    op.drop_constraint(
        op.f("ck_dataset_versions_ck_valid_publication_state"),
        "dataset_versions",
        type_="check",
    )

    op.drop_constraint(
        op.f("ck_dataset_versions_dataset_version_status_values"),
        "dataset_versions",
        type_="check",
    )

    # 6. Add the expanded status constraint.
    op.create_check_constraint(
        op.f("ck_dataset_versions_dataset_version_status_values"),
        "dataset_versions",
        """
        status IN (
            'staging',
            'verified',
            'available',
            'failed',
            'abandoned'
        )
        """,
    )

    # 7. Enforce verification and publication lifecycle rules.
    op.create_check_constraint(
        op.f("ck_dataset_versions_valid_publication_state"),
        "dataset_versions",
        """
        (
            status = 'available'
            AND (record_count IS NULL OR record_count > 0)
            AND storage_uri IS NOT NULL
            AND checksum_algorithm IS NOT NULL
            AND checksum IS NOT NULL
            AND published_at IS NOT NULL
            AND (
                verified_at IS NULL
                OR verified_at <= published_at
            )
        )
        OR
        (
            status = 'verified'
            AND (record_count IS NULL OR record_count > 0)
            AND storage_uri IS NOT NULL
            AND checksum_algorithm IS NOT NULL
            AND checksum IS NOT NULL
            AND verified_at IS NOT NULL
            AND published_at IS NULL
        )
        OR
        (
            status = 'abandoned'
            AND published_at IS NULL
        )
        OR
        (
            status IN ('staging', 'failed')
            AND verified_at IS NULL
            AND published_at IS NULL
        )
        """,
    )

    # 8. Verification cannot precede version creation.
    op.create_check_constraint(
        op.f("ck_dataset_versions_verification_after_creation"),
        "dataset_versions",
        """
        verified_at IS NULL
        OR verified_at >= created_at
        """,
    )


def downgrade() -> None:
    """Safely remove the ingestion attempt recovery model."""

    bind = op.get_bind()

    # Refuse downgrade if it would destroy recovery or lifecycle data.
    has_recovery_data = bind.execute(
        sa.text(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM ingestion_attempts
                )
                OR EXISTS (
                    SELECT 1
                    FROM ingestion_jobs
                    WHERE current_attempt_id IS NOT NULL
                )
                OR EXISTS (
                    SELECT 1
                    FROM dataset_versions
                    WHERE
                        status IN ('verified', 'abandoned')
                        OR verified_at IS NOT NULL
                        OR ingestion_attempt_id IS NOT NULL
                )
            """
        )
    ).scalar_one()

    if has_recovery_data:
        raise RuntimeError(
            "Cannot downgrade ingestion attempt recovery migration: "
            "recovery records or new dataset-version lifecycle data "
            "exist. Preserve this data before attempting a downgrade."
        )

    # 1. Remove new lifecycle constraints.
    op.drop_constraint(
        op.f("ck_dataset_versions_verification_after_creation"),
        "dataset_versions",
        type_="check",
    )

    op.drop_constraint(
        op.f("ck_dataset_versions_valid_publication_state"),
        "dataset_versions",
        type_="check",
    )

    op.drop_constraint(
        op.f("ck_dataset_versions_dataset_version_status_values"),
        "dataset_versions",
        type_="check",
    )

    # 2. Restore the previous status constraint.
    op.create_check_constraint(
        op.f("ck_dataset_versions_dataset_version_status_values"),
        "dataset_versions",
        """
        status IN (
            'staging',
            'available',
            'failed'
        )
        """,
    )

    # 3. Restore the previous publication constraint.
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

    # 4. Remove job-to-attempt relationship.
    op.drop_constraint(
    op.f("fk_ingestion_jobs_current_attempt"),
    "ingestion_jobs",
    type_="foreignkey",
)

    op.drop_column("ingestion_jobs", "current_attempt_id")

    # 5. Remove dataset-version attempt relationship.
    op.drop_constraint(
        op.f(
            "fk_dataset_versions_ingestion_attempt_id_ingestion_attempts"
        ),
        "dataset_versions",
        type_="foreignkey",
    )

    op.drop_column("dataset_versions", "ingestion_attempt_id")
    op.drop_column("dataset_versions", "verified_at")

    # 6. Remove attempt index and table.
    op.drop_index(
        op.f("ix_ingestion_attempts_job_id_status"),
        table_name="ingestion_attempts",
    )

    op.drop_table("ingestion_attempts")