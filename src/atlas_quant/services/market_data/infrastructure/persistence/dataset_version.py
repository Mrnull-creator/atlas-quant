"""SQLAlchemy persistence mapping for immutable dataset versions."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from atlas_quant.services.market_data.domain.datasets import (
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)


class DatasetVersionORM(MarketDataBase):
    """Database representation of an immutable dataset snapshot."""

    __tablename__ = "dataset_versions"

    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "version_number",
            name="uq_dataset_versions_dataset_version_number",
        ),
        UniqueConstraint(
            "dataset_id",
            "dataset_version_id",
            name="uq_dataset_versions_dataset_version",
        ),
        CheckConstraint(
            "version_number >= 1",
            name="version_number_positive",
        ),
        CheckConstraint(
            "record_count >= 0",
            name="record_count_nonnegative",
        ),
        CheckConstraint(
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
            name="valid_observation_boundaries",
        ),
        CheckConstraint(
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
            name="checksum_metadata_pair",
        ),
        CheckConstraint(
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
            name="valid_publication_state",
        ),
        CheckConstraint(
            "published_at IS NULL OR published_at >= created_at",
            name="publication_after_creation",
        ),
    )

    dataset_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "datasets.dataset_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    version_number: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    schema_version: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    storage_format: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    status: Mapped[DatasetVersionStatus] = mapped_column(
        Enum(
            DatasetVersionStatus,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            native_enum=False,
            create_constraint=True,
            name="dataset_version_status_values",
        ),
        nullable=False,
        default=DatasetVersionStatus.STAGING,
        server_default=DatasetVersionStatus.STAGING.value,
    )

    first_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    record_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    storage_uri: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    checksum_algorithm: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    checksum: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )