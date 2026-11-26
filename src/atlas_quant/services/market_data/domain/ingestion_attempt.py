"""Domain model for an individual market data ingestion execution attempt."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


class IngestionAttemptStatus(StrEnum):
    """Lifecycle states of an individual ingestion execution attempt."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LEASE_EXPIRED = "lease_expired"
    SUPERSEDED = "superseded"


TERMINAL_STATUSES = frozenset(
    {
        IngestionAttemptStatus.SUCCEEDED,
        IngestionAttemptStatus.PARTIALLY_SUCCEEDED,
        IngestionAttemptStatus.FAILED,
        IngestionAttemptStatus.CANCELLED,
        IngestionAttemptStatus.LEASE_EXPIRED,
        IngestionAttemptStatus.SUPERSEDED,
    }
)


@dataclass(frozen=True, slots=True)
class IngestionAttempt:
    """Represents one worker's execution attempt for an ingestion job.

    An ingestion job represents the logical request to acquire and process
    market data. An ingestion attempt represents one specific execution
    of that request by a worker.

    A job can have multiple attempts over its lifetime. Each attempt has
    its own identity, monotonically increasing attempt number, and fencing
    token.

    The fencing token identifies the execution authority granted to this
    attempt. Later attempts receive higher tokens. Persistence and
    application services must verify that an attempt still owns the
    current fencing token before allowing it to modify job or publication
    state.

    Timestamps are timezone-aware and normalized to UTC.

    This model does not implement lease acquisition, heartbeat renewal,
    execution ownership, or persistence. Those responsibilities belong
    to subsequent application and infrastructure components.
    """

    job_id: UUID
    worker_id: str

    attempt_number: int
    fencing_token: int

    started_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime

    status: IngestionAttemptStatus = IngestionAttemptStatus.RUNNING

    attempt_id: UUID = field(default_factory=uuid4)

    finished_at: datetime | None = None

    failure_code: str | None = None
    failure_message: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize ingestion attempt metadata."""

        self._validate_identity()

        self._validate_worker_id()

        self._validate_positive_integer(
            "attempt_number",
            self.attempt_number,
        )
        self._validate_positive_integer(
            "fencing_token",
            self.fencing_token,
        )

        if not isinstance(self.status, IngestionAttemptStatus):
            raise TypeError(
                "status must be an IngestionAttemptStatus value."
            )

        self._normalize_timestamps()

        self._validate_optional_text(
            "failure_code",
            self.failure_code,
        )
        self._validate_optional_text(
            "failure_message",
            self.failure_message,
        )

        self._validate_timestamp_order()

        self._validate_lifecycle()

    def _validate_identity(self) -> None:
        """Validate the UUID identities associated with this attempt."""

        if not isinstance(self.attempt_id, UUID):
            raise TypeError("attempt_id must be a UUID.")

        if not isinstance(self.job_id, UUID):
            raise TypeError("job_id must be a UUID.")

    def _validate_worker_id(self) -> None:
        """Validate and normalize the worker identity."""

        if not isinstance(self.worker_id, str):
            raise TypeError("worker_id must be a string.")

        worker_id = self.worker_id.strip()

        if not worker_id:
            raise ValueError("worker_id cannot be empty.")

        object.__setattr__(self, "worker_id", worker_id)

    @staticmethod
    def _validate_positive_integer(
        field_name: str,
        value: int,
    ) -> None:
        """Require a positive integer, explicitly rejecting booleans."""

        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field_name} must be an integer.")

        if value < 1:
            raise ValueError(f"{field_name} must be at least 1.")

    def _normalize_timestamps(self) -> None:
        """Validate timestamps and normalize them to UTC."""

        required_fields = (
            "started_at",
            "heartbeat_at",
            "lease_expires_at",
        )

        for field_name in required_fields:
            value = getattr(self, field_name)

            normalized = self._normalize_timestamp(
                field_name,
                value,
            )

            object.__setattr__(
                self,
                field_name,
                normalized,
            )

        if self.finished_at is not None:
            normalized = self._normalize_timestamp(
                "finished_at",
                self.finished_at,
            )

            object.__setattr__(
                self,
                "finished_at",
                normalized,
            )

    @staticmethod
    def _normalize_timestamp(
        field_name: str,
        value: datetime,
    ) -> datetime:
        """Validate a required timestamp and return its UTC equivalent."""

        if not isinstance(value, datetime):
            raise TypeError(
                f"{field_name} must be a datetime value."
            )

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"{field_name} must include timezone information."
            )

        return value.astimezone(timezone.utc)

    def _validate_timestamp_order(self) -> None:
        """Enforce chronological consistency between attempt timestamps."""

        if self.heartbeat_at < self.started_at:
            raise ValueError(
                "heartbeat_at cannot be before started_at."
            )

        if self.lease_expires_at <= self.heartbeat_at:
            raise ValueError(
                "lease_expires_at must be after heartbeat_at."
            )

        if self.finished_at is not None:
            if self.finished_at < self.started_at:
                raise ValueError(
                    "finished_at cannot be before started_at."
                )

            if self.finished_at < self.heartbeat_at:
                raise ValueError(
                    "finished_at cannot be before heartbeat_at."
                )

    def _validate_lifecycle(self) -> None:
        """Enforce status-specific execution lifecycle requirements."""

        if self.status == IngestionAttemptStatus.RUNNING:
            if self.finished_at is not None:
                raise ValueError(
                    "Running attempts cannot have finished_at."
                )

            if (
                self.failure_code is not None
                or self.failure_message is not None
            ):
                raise ValueError(
                    "Running attempts cannot have failure details."
                )

            return

        if self.status in TERMINAL_STATUSES:
            if self.finished_at is None:
                raise ValueError(
                    "Terminal attempts require finished_at."
                )

        if self.status == IngestionAttemptStatus.SUCCEEDED:
            if (
                self.failure_code is not None
                or self.failure_message is not None
            ):
                raise ValueError(
                    "Successful attempts cannot have failure details."
                )

        elif self.status in (
            IngestionAttemptStatus.FAILED,
            IngestionAttemptStatus.PARTIALLY_SUCCEEDED,
        ):
            if self.failure_message is None:
                raise ValueError(
                    "Failed and partially successful attempts "
                    "require failure_message."
                )

        elif self.status == IngestionAttemptStatus.CANCELLED:
            if (
                self.failure_code is not None
                or self.failure_message is not None
            ):
                raise ValueError(
                    "Cancelled attempts cannot have failure details."
                )

    def _validate_optional_text(
        self,
        field_name: str,
        value: str | None,
    ) -> None:
        """Validate and normalize an optional text field."""

        if value is None:
            return

        if not isinstance(value, str):
            raise TypeError(
                f"{field_name} must be a string or None."
            )

        normalized = value.strip()

        if not normalized:
            raise ValueError(
                f"{field_name} cannot be empty."
            )

        object.__setattr__(
            self,
            field_name,
            normalized,
        )

    @property
    def is_terminal(self) -> bool:
        """Return whether this attempt has reached a terminal state."""

        return self.status in TERMINAL_STATUSES