"""Domain models for market data validation reports."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
)


class ValidationOutcome(StrEnum):
    """Derived outcomes of a validation report."""

    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Records the result of validating a market data dataset or ingestion."""

    validator_name: str
    validator_version: str
    quality_report: DataQualityReport

    report_id: UUID = field(default_factory=uuid4)

    ingestion_job_id: UUID | None = None
    dataset_version_id: UUID | None = None

    validated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    report_version: str = "1.0"

    def __post_init__(self) -> None:
        """Validate and normalize validation report metadata."""

        if not isinstance(self.report_id, UUID):
            raise TypeError("report_id must be a UUID.")

        if self.ingestion_job_id is not None:
            if not isinstance(self.ingestion_job_id, UUID):
                raise TypeError("ingestion_job_id must be a UUID or None.")

        if self.dataset_version_id is not None:
            if not isinstance(self.dataset_version_id, UUID):
                raise TypeError(
                    "dataset_version_id must be a UUID or None."
                )

        if (
            self.ingestion_job_id is None
            and self.dataset_version_id is None
        ):
            raise ValueError(
                "A validation report must reference an ingestion job "
                "or a dataset version."
            )

        for field_name in (
            "validator_name",
            "validator_version",
            "report_version",
        ):
            value = getattr(self, field_name)

            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string.")

            normalized = value.strip()

            if not normalized:
                raise ValueError(f"{field_name} cannot be empty.")

            object.__setattr__(self, field_name, normalized)

        if not isinstance(self.quality_report, DataQualityReport):
            raise TypeError(
                "quality_report must be a DataQualityReport value."
            )

        if not isinstance(self.validated_at, datetime):
            raise TypeError("validated_at must be a datetime value.")

        if (
            self.validated_at.tzinfo is None
            or self.validated_at.utcoffset() is None
        ):
            raise ValueError(
                "validated_at must include timezone information."
            )

        object.__setattr__(
            self,
            "validated_at",
            self.validated_at.astimezone(timezone.utc),
        )

    @property
    def outcome(self) -> ValidationOutcome:
        """Derive the outcome from the quality findings."""

        if not self.quality_report.is_valid:
            return ValidationOutcome.INVALID

        if self.quality_report.has_warnings:
            return ValidationOutcome.VALID_WITH_WARNINGS

        return ValidationOutcome.VALID
