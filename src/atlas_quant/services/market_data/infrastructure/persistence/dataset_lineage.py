"""SQLAlchemy persistence mapping for dataset-version lineage."""

from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from atlas_quant.services.market_data.domain.dataset_lineage import (
    DatasetLineageRelationship,
    DatasetLineageSourceType,
)
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)


class DatasetVersionLineageORM(MarketDataBase):
    """Database representation of an immutable lineage relationship."""

    __tablename__ = "dataset_version_lineage"

    __table_args__ = (
        CheckConstraint(
            """
            (
                source_type = 'ingestion_job'
                AND ingestion_job_id IS NOT NULL
                AND source_dataset_version_id IS NULL
                AND relationship = 'ingested_from'
            )
            OR
            (
                source_type = 'dataset_version'
                AND ingestion_job_id IS NULL
                AND source_dataset_version_id IS NOT NULL
                AND relationship = 'derived_from'
                AND source_dataset_version_id <> target_dataset_version_id
            )
            """,
            name="valid_source_relationship",
        ),
    )

    lineage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    target_dataset_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "dataset_versions.dataset_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    source_type: Mapped[DatasetLineageSourceType] = mapped_column(
        Enum(
            DatasetLineageSourceType,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            native_enum=False,
            create_constraint=True,
            name="dataset_lineage_source_type_values",
        ),
        nullable=False,
    )

    ingestion_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "ingestion_jobs.job_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    source_dataset_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "dataset_versions.dataset_version_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    relationship_type: Mapped[DatasetLineageRelationship] = mapped_column(
        "relationship",
        Enum(
            DatasetLineageRelationship,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            native_enum=False,
            create_constraint=True,
            name="dataset_lineage_relationship_values",
        ),
        nullable=False,
    )