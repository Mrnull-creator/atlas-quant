
"""SQLAlchemy persistence mapping for ingestion execution attempts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from atlas_quant.services.market_data.domain.ingestion_attempt import (
    IngestionAttemptStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)


class IngestionAttemptORM(MarketDataBase):
    """Database representation of one ingestion execution attempt."""

    __tablename__ = "ingestion_attempts"

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_ingestion_attempts_job_attempt_number",
        ),
        UniqueConstraint(
            "job_id",
            "fencing_token",
            name="uq_ingestion_attempts_job_fencing_token",
        ),
        UniqueConstraint(
            "job_id",
            "attempt_id",
            name="uq_ingestion_attempts_job_attempt",
        ),
        CheckConstraint(
            "attempt_number >= 1",
            name="attempt_number_positive",
        ),
        CheckConstraint(
            "fencing_token >= 1",
            name="fencing_token_positive",
        ),
        CheckConstraint(
            "length(trim(worker_id)) > 0",
            name="worker_id_nonempty",
        ),
        CheckConstraint(
            "heartbeat_at >= started_at",
            name="heartbeat_after_start",
        ),
        CheckConstraint(
            "lease_expires_at > heartbeat_at",
            name="lease_after_heartbeat",
        ),
        CheckConstraint(
            """
            finished_at IS NULL
            OR finished_at >= started_at
            """,
            name="finish_after_start",
        ),
        CheckConstraint(
            """
            finished_at IS NULL
            OR finished_at >= heartbeat_at
            """,
            name="finish_after_heartbeat",
        ),
        CheckConstraint(
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
            name="valid_attempt_lifecycle",
        ),
        Index(
            "ix_ingestion_attempts_job_id_status",
            "job_id",
            "status",
        ),
    )

    attempt_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "ingestion_jobs.job_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    attempt_number: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    fencing_token: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    worker_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    status: Mapped[IngestionAttemptStatus] = mapped_column(
        Enum(
            IngestionAttemptStatus,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            native_enum=False,
            create_constraint=True,
            length=20,
            name="ingestion_attempt_status_values",
        ),
        nullable=False,
        default=IngestionAttemptStatus.RUNNING,
        server_default=IngestionAttemptStatus.RUNNING.value,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    failure_code: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    failure_message: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )