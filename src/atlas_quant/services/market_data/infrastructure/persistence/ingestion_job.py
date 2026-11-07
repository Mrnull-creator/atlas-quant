"""SQLAlchemy persistence mappings for market data ingestion jobs."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJobStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)


class IngestionJobORM(MarketDataBase):
    """Database representation of an ingestion job."""

    __tablename__ = "ingestion_jobs"

    __table_args__ = (
        CheckConstraint(
            "requested_start < requested_end",
            name="valid_requested_range",
        ),
        CheckConstraint(
            "records_downloaded >= 0",
            name="records_downloaded_nonnegative",
        ),
        CheckConstraint(
            "records_written >= 0",
            name="records_written_nonnegative",
        ),
        CheckConstraint(
            "records_rejected >= 0",
            name="records_rejected_nonnegative",
        ),
        CheckConstraint(
            """
            (
                status = 'pending'
                AND started_at IS NULL
                AND completed_at IS NULL
                AND failure_code IS NULL
                AND failure_message IS NULL
            )
            OR
            (
                status = 'running'
                AND started_at IS NOT NULL
                AND completed_at IS NULL
                AND failure_code IS NULL
                AND failure_message IS NULL
            )
            OR
            (
                status = 'succeeded'
                AND completed_at IS NOT NULL
                AND failure_code IS NULL
                AND failure_message IS NULL
            )
            OR
            (
                status = 'partially_succeeded'
                AND completed_at IS NOT NULL
                AND failure_message IS NOT NULL
            )
            OR
            (
                status = 'failed'
                AND completed_at IS NOT NULL
                AND failure_message IS NOT NULL
            )
            OR
            (
                status = 'cancelled'
                AND completed_at IS NOT NULL
                AND failure_code IS NULL
                AND failure_message IS NULL
            )
            """,
            name="valid_lifecycle_state",
        ),
        CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="start_after_creation",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="completion_after_creation",
        ),
        CheckConstraint(
            """
            completed_at IS NULL
            OR started_at IS NULL
            OR completed_at >= started_at
            """,
            name="completion_after_start",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    provider_id: Mapped[str] = mapped_column(
        String,
        ForeignKey(
            "market_data_providers.provider_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    asset_class: Mapped[AssetClass] = mapped_column(
        Enum(
            AssetClass,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            native_enum=False,
            create_constraint=True,
            name="ingestion_asset_class_values",
        ),
        nullable=False,
    )

    instrument_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "instruments.instrument_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    instrument_symbol: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    timeframe: Mapped[Timeframe | None] = mapped_column(
        Enum(
            Timeframe,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            native_enum=False,
            create_constraint=True,
            name="ingestion_timeframe_values",
        ),
        nullable=True,
    )

    requested_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    requested_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    status: Mapped[IngestionJobStatus] = mapped_column(
        Enum(
            IngestionJobStatus,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            native_enum=False,
            create_constraint=True,
            name="ingestion_job_status_values",
        ),
        nullable=False,
        default=IngestionJobStatus.PENDING,
        server_default=IngestionJobStatus.PENDING.value,
    )

    records_downloaded: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    records_written: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    records_rejected: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    failure_code: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    failure_message: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class IngestionJobDatasetVersionORM(MarketDataBase):
    """Associates ingestion jobs with their produced dataset versions."""

    __tablename__ = "ingestion_job_dataset_versions"

    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "ingestion_jobs.job_id",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )

    dataset_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "dataset_versions.dataset_version_id",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
