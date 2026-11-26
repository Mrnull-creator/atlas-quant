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
            raise TypeError("severity must be a FindingSeverity value.")

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
    """Summarizes quality findings, including details omitted from retention."""

    checked_records: int
    findings: tuple[QualityFinding, ...] = ()
    total_error_count: int | None = None
    total_warning_count: int | None = None
    total_info_count: int | None = None
    findings_truncated: bool = False

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

        retained_counts = {
            FindingSeverity.ERROR: sum(
                finding.severity == FindingSeverity.ERROR
                for finding in normalized_findings
            ),
            FindingSeverity.WARNING: sum(
                finding.severity == FindingSeverity.WARNING
                for finding in normalized_findings
            ),
            FindingSeverity.INFO: sum(
                finding.severity == FindingSeverity.INFO
                for finding in normalized_findings
            ),
        }

        total_fields = (
            ("total_error_count", FindingSeverity.ERROR),
            ("total_warning_count", FindingSeverity.WARNING),
            ("total_info_count", FindingSeverity.INFO),
        )

        for field_name, severity in total_fields:
            total = getattr(self, field_name)

            if total is None:
                total = retained_counts[severity]
            elif isinstance(total, bool) or not isinstance(total, int):
                raise TypeError(f"{field_name} must be an integer.")

            if total < 0:
                raise ValueError(f"{field_name} cannot be negative.")

            if total < retained_counts[severity]:
                raise ValueError(
                    f"{field_name} cannot be less than retained findings."
                )

            object.__setattr__(self, field_name, total)

        if not isinstance(self.findings_truncated, bool):
            raise TypeError("findings_truncated must be a boolean.")

        details_omitted = (
            self.total_error_count > retained_counts[FindingSeverity.ERROR]
            or self.total_warning_count
            > retained_counts[FindingSeverity.WARNING]
            or self.total_info_count > retained_counts[FindingSeverity.INFO]
        )

        if details_omitted:
            object.__setattr__(self, "findings_truncated", True)

    @property
    def error_count(self) -> int:
        """Return the total number of error findings."""
        return self.total_error_count

    @property
    def warning_count(self) -> int:
        """Return the total number of warning findings."""
        return self.total_warning_count

    @property
    def info_count(self) -> int:
        """Return the total number of informational findings."""
        return self.total_info_count

    @property
    def total_findings(self) -> int:
        """Return the total number of findings detected."""
        return self.error_count + self.warning_count + self.info_count

    @property
    def retained_finding_count(self) -> int:
        """Return the number of detailed findings retained."""
        return len(self.findings)

    @property
    def is_valid(self) -> bool:
        """Return whether the report contains no errors."""
        return self.error_count == 0

    @property
    def has_warnings(self) -> bool:
        """Return whether the report contains warnings."""
        return self.warning_count > 0


class QualityFindingAccumulator:
    """Count every finding while retaining a bounded number of details."""

    def __init__(self, max_findings: int = 1_000) -> None:
        if (
            isinstance(max_findings, bool)
            or not isinstance(max_findings, int)
        ):
            raise TypeError("max_findings must be an integer.")

        if max_findings < 1:
            raise ValueError("max_findings must be at least 1.")

        self._max_findings = max_findings
        self._findings: list[QualityFinding] = []
        self._error_count = 0
        self._warning_count = 0
        self._info_count = 0

    @property
    def findings(self) -> tuple[QualityFinding, ...]:
        """Return the retained finding details."""
        return tuple(self._findings)

    def add(self, finding: QualityFinding) -> None:
        """Count a finding and retain its detail when capacity permits."""

        if not isinstance(finding, QualityFinding):
            raise TypeError("finding must be a QualityFinding value.")

        self._increment(finding.severity)
        self._retain(finding)

    def merge_report(self, report: DataQualityReport) -> None:
        """Merge report totals and retain available details within the limit."""

        if not isinstance(report, DataQualityReport):
            raise TypeError("report must be a DataQualityReport.")

        self._error_count += report.error_count
        self._warning_count += report.warning_count
        self._info_count += report.info_count

        for finding in report.findings:
            self._retain(finding)

    def build_report(self, checked_records: int) -> DataQualityReport:
        """Build an immutable report containing totals and retained details."""

        return DataQualityReport(
            checked_records=checked_records,
            findings=tuple(self._findings),
            total_error_count=self._error_count,
            total_warning_count=self._warning_count,
            total_info_count=self._info_count,
            findings_truncated=(
                self._error_count
                + self._warning_count
                + self._info_count
                > len(self._findings)
            ),
        )

    def _increment(self, severity: FindingSeverity) -> None:
        if severity == FindingSeverity.ERROR:
            self._error_count += 1
        elif severity == FindingSeverity.WARNING:
            self._warning_count += 1
        else:
            self._info_count += 1

    def _retain(self, finding: QualityFinding) -> None:
        if len(self._findings) < self._max_findings:
            self._findings.append(finding)