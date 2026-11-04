
"""Domain model for dataset-version lineage."""

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4


class DatasetLineageSourceType(StrEnum):
    """Identifies the kind of source contributing to a dataset version."""

    INGESTION_JOB = "ingestion_job"
    DATASET_VERSION = "dataset_version"


class DatasetLineageRelationship(StrEnum):
    """Describes how a source contributes to a dataset version."""

    INGESTED_FROM = "ingested_from"
    DERIVED_FROM = "derived_from"


@dataclass(frozen=True, slots=True)
class DatasetVersionLineage:
    """Records one source-to-target dataset-version relationship."""

    target_dataset_version_id: UUID
    source_type: DatasetLineageSourceType
    source_id: UUID

    relationship: DatasetLineageRelationship

    lineage_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        """Validate dataset-version lineage metadata."""

        if not isinstance(self.lineage_id, UUID):
            raise TypeError("lineage_id must be a UUID.")

        if not isinstance(self.target_dataset_version_id, UUID):
            raise TypeError(
                "target_dataset_version_id must be a UUID."
            )

        if not isinstance(self.source_id, UUID):
            raise TypeError("source_id must be a UUID.")

        if not isinstance(self.source_type, DatasetLineageSourceType):
            raise TypeError(
                "source_type must be a DatasetLineageSourceType value."
            )

        if not isinstance(
            self.relationship,
            DatasetLineageRelationship,
        ):
            raise TypeError(
                "relationship must be a DatasetLineageRelationship value."
            )

        if (
            self.source_type == DatasetLineageSourceType.INGESTION_JOB
            and self.relationship != DatasetLineageRelationship.INGESTED_FROM
        ):
            raise ValueError(
                "Ingestion job sources must use INGESTED_FROM."
            )

        if (
            self.source_type == DatasetLineageSourceType.DATASET_VERSION
            and self.relationship != DatasetLineageRelationship.DERIVED_FROM
        ):
            raise ValueError(
                "Dataset version sources must use DERIVED_FROM."
            )

        if (
            self.source_type == DatasetLineageSourceType.DATASET_VERSION
            and self.source_id == self.target_dataset_version_id
        ):
            raise ValueError(
                "A dataset version cannot be its own lineage source."
            )