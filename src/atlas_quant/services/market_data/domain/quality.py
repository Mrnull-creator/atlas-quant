
"""Domain models for market data quality assessment."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class FindingSeverity(StrEnum):
    """Severity levels for market data quality findings."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """Represents one detected market data quality issue."""

    code: str
    severity: FindingSeverity
    message: str

    record_index: int | None = None
    field: str | None = None
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        """Validate and normalize a quality finding."""

        if not isinstance(self.code, str):
            raise TypeError("Finding code must be a string.")

        normalized_code = self.code.strip().upper()

        if not normalized_code:
            raise ValueError("Finding code cannot be empty.")

        object.__setattr__(self, "code", normalized_code)

        if not isinstance(self.severity, FindingSeverity):
            raise TypeError(
                "severity must be a FindingSeverity value."
            )

        if not isinstance(self.message, str):
            raise TypeError("Finding message must be a string.")

        normalized_message = self.message.strip()

        if not normalized_message:
            raise ValueError("Finding message cannot be empty.")

        object.__setattr__(self, "message", normalized_message)

        if self.record_index is not None:
            if (
                isinstance(self.record_index, bool)
                or not isinstance(self.record_index, int)
            ):
                raise TypeError("record_index must be an integer.")

            if self.record_index < 0:
                raise ValueError("record_index cannot be negative.")

        if self.field is not None:
            if not isinstance(self.field, str):
                raise TypeError("field must be a string.")

            normalized_field = self.field.strip()

            if not normalized_field:
                raise ValueError("field cannot be empty.")

            object.__setattr__(self, "field", normalized_field)

        if self.timestamp is not None:
            if not isinstance(self.timestamp, datetime):
                raise TypeError("timestamp must be a datetime value.")

            if (
                self.timestamp.tzinfo is None
                or self.timestamp.utcoffset() is None
            ):
                raise ValueError(
                    "Finding timestamp must include timezone information."
                )

            object.__setattr__(
                self,
                "timestamp",
                self.timestamp.astimezone(timezone.utc),
            )


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    """Summarizes the quality assessment of a data batch."""

    checked_records: int
    findings: tuple[QualityFinding, ...] = ()

    def __post_init__(self) -> None:
        """Validate the quality report."""

        if (
            isinstance(self.checked_records, bool)
            or not isinstance(self.checked_records, int)
        ):
            raise TypeError("checked_records must be an integer.")

        if self.checked_records < 0:
            raise ValueError("checked_records cannot be negative.")

        if not isinstance(self.findings, (tuple, list)):
            raise TypeError("findings must be a collection.")

        normalized_findings = tuple(self.findings)

        for finding in normalized_findings:
            if not isinstance(finding, QualityFinding):
                raise TypeError(
                    "Every finding must be a QualityFinding value."
                )

            if (
                finding.record_index is not None
                and finding.record_index >= self.checked_records
            ):
                raise ValueError(
                    "Finding record_index cannot exceed the "
                    "available record range."
                )

        object.__setattr__(self, "findings", normalized_findings)

    @property
    def error_count(self) -> int:
        """Return the number of error findings."""

        return sum(
            finding.severity == FindingSeverity.ERROR
            for finding in self.findings
        )

    @property
    def warning_count(self) -> int:
        """Return the number of warning findings."""

        return sum(
            finding.severity == FindingSeverity.WARNING
            for finding in self.findings
        )

    @property
    def info_count(self) -> int:
        """Return the number of informational findings."""

        return sum(
            finding.severity == FindingSeverity.INFO
            for finding in self.findings
        )

    @property
    def is_valid(self) -> bool:
        """Return whether the report contains no errors."""

        return self.error_count == 0

    @property
    def has_warnings(self) -> bool:
        """Return whether the report contains warnings."""

        return self.warning_count > 0