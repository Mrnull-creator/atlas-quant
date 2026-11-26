"""Enforce execution attempt and fill integrity.

Revision ID: d6b3a9f14c20
Revises: c4e8f1a2d963
"""

from alembic import op
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import UUID


revision = "d6b3a9f14c20"
down_revision = "c4e8f1a2d963"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The composite foreign key must reference a unique column combination.
    op.create_unique_constraint(
        "uq_execution_attempts_attempt_account_order_fill",
        "execution_attempts",
        ["attempt_id", "account_id", "order_id", "fill_id"],
        schema="execution",
    )

    # Legacy fills remain valid with a NULL execution_attempt_id.
    op.add_column(
        "fills",
        Column("execution_attempt_id", UUID(as_uuid=True), nullable=True),
        schema="execution",
    )

    op.create_foreign_key(
        "fk_fills_execution_attempt_identity",
        "fills",
        "execution_attempts",
        ["execution_attempt_id", "account_id", "order_id", "fill_id"],
        ["attempt_id", "account_id", "order_id", "fill_id"],
        source_schema="execution",
        referent_schema="execution",
        ondelete="RESTRICT",
    )

    op.create_index(
        "uq_execution_fills_execution_attempt_id",
        "fills",
        ["execution_attempt_id"],
        unique=True,
        schema="execution",
    )

    # The attempt primary identity must never change.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION
        execution.prevent_execution_attempt_identity_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.attempt_id IS DISTINCT FROM OLD.attempt_id THEN
                RAISE EXCEPTION
                    'Execution attempt identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_execution_attempt_identity_immutable
        BEFORE UPDATE ON execution.execution_attempts
        FOR EACH ROW
        EXECUTE FUNCTION
        execution.prevent_execution_attempt_identity_mutation();
        """
    )

    # A prepared attempt may transition once to completed or failed.
    # Terminal attempts cannot subsequently be changed or deleted.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION
        execution.prevent_execution_attempt_lifecycle_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'prepared' THEN
                    RAISE EXCEPTION
                        'Terminal execution attempts cannot be deleted';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.status <> 'prepared' THEN
                RAISE EXCEPTION
                    'Terminal execution attempts cannot be modified';
            END IF;

            IF NEW.status NOT IN ('completed', 'failed') THEN
                RAISE EXCEPTION
                    'Execution attempts may transition only from prepared to completed or failed';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_execution_attempt_lifecycle_immutable
        BEFORE UPDATE OR DELETE
        ON execution.execution_attempts
        FOR EACH ROW
        EXECUTE FUNCTION
        execution.prevent_execution_attempt_lifecycle_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS
        trg_execution_attempt_identity_immutable
        ON execution.execution_attempts;
        """
    )

    op.execute(
        """
        DROP FUNCTION IF EXISTS
        execution.prevent_execution_attempt_identity_mutation();
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS
        trg_execution_attempt_lifecycle_immutable
        ON execution.execution_attempts;
        """
    )

    op.execute(
        """
        DROP FUNCTION IF EXISTS
        execution.prevent_execution_attempt_lifecycle_mutation();
        """
    )

    op.drop_index(
        "uq_execution_fills_execution_attempt_id",
        table_name="fills",
        schema="execution",
    )

    op.drop_constraint(
        "fk_fills_execution_attempt_identity",
        "fills",
        schema="execution",
        type_="foreignkey",
    )

    op.drop_column(
        "fills",
        "execution_attempt_id",
        schema="execution",
    )

    op.drop_constraint(
        "uq_execution_attempts_attempt_account_order_fill",
        "execution_attempts",
        schema="execution",
        type_="unique",
    )
