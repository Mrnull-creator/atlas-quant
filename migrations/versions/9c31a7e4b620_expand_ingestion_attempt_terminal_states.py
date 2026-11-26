"""expand ingestion attempt terminal states

Revision ID: 9c31a7e4b620
Revises: 7521178cd761
Create Date: 2026-11-17
"""

from alembic import op
import sqlalchemy as sa


revision = "9c31a7e4b620"
down_revision = "7521178cd761"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Support partial-success and cancellation attempt outcomes."""

    # The longest new status is "partially_succeeded" (20 characters).
    op.alter_column(
        "ingestion_attempts",
        "status",
        existing_type=sa.String(length=13),
        type_=sa.String(length=20),
        existing_nullable=False,
        existing_server_default="running",
    )

    op.drop_constraint(
        op.f("ck_ingestion_attempts_valid_attempt_lifecycle"),
        "ingestion_attempts",
        type_="check",
    )

    op.drop_constraint(
        op.f(
            "ck_ingestion_attempts_ingestion_attempt_status_values"
        ),
        "ingestion_attempts",
        type_="check",
    )

    op.create_check_constraint(
        op.f("ck_ingestion_attempts_ingestion_attempt_status_values"),
        "ingestion_attempts",
        """
        status IN (
            'running',
            'succeeded',
            'partially_succeeded',
            'failed',
            'cancelled',
            'lease_expired',
            'superseded'
        )
        """,
    )

    op.create_check_constraint(
        op.f("ck_ingestion_attempts_valid_attempt_lifecycle"),
        "ingestion_attempts",
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
            status = 'partially_succeeded'
            AND finished_at IS NOT NULL
            AND failure_message IS NOT NULL
        )
        OR
        (
            status = 'failed'
            AND finished_at IS NOT NULL
            AND failure_message IS NOT NULL
        )
        OR
        (
            status = 'cancelled'
            AND finished_at IS NOT NULL
            AND failure_code IS NULL
            AND failure_message IS NULL
        )
        OR
        (
            status IN ('lease_expired', 'superseded')
            AND finished_at IS NOT NULL
        )
        """,
    )


def downgrade() -> None:
    """Restore the previous attempt lifecycle constraint safely."""

    bind = op.get_bind()

    has_new_statuses = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM ingestion_attempts
                WHERE status IN (
                    'partially_succeeded',
                    'cancelled'
                )
            )
            """
        )
    ).scalar_one()

    if has_new_statuses:
        raise RuntimeError(
            "Cannot downgrade ingestion attempt terminal states: "
            "partially_succeeded or cancelled attempts exist."
        )

    op.drop_constraint(
        op.f("ck_ingestion_attempts_valid_attempt_lifecycle"),
        "ingestion_attempts",
        type_="check",
    )

    op.drop_constraint(
        op.f(
            "ck_ingestion_attempts_ingestion_attempt_status_values"
        ),
        "ingestion_attempts",
        type_="check",
    )

    op.create_check_constraint(
        op.f("ck_ingestion_attempts_ingestion_attempt_status_values"),
        "ingestion_attempts",
        """
        status IN (
            'running',
            'succeeded',
            'failed',
            'lease_expired',
            'superseded'
        )
        """,
    )

    op.create_check_constraint(
        op.f("ck_ingestion_attempts_valid_attempt_lifecycle"),
        "ingestion_attempts",
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
    )

    op.alter_column(
        "ingestion_attempts",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=13),
        existing_nullable=False,
        existing_server_default="running",
    )