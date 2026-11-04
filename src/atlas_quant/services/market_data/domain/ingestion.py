
"""Domain models for market data ingestion jobs."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)


class IngestionJobStatus(StrEnum):
    """Lifecycle states of an ingestion job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {
        IngestionJobStatus.SUCCEEDED,
        IngestionJobStatus.PARTIALLY_SUCCEEDED,
        IngestionJobStatus.FAILED,
        IngestionJobStatus.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class IngestionJob:
    """Records one attempt to acquire and process market data.

    A job may produce multiple dataset versions. The job records their
    identities but does not own or modify those dataset versions.
    """

    provider_id: str
    asset_class: AssetClass

    requested_start: datetime
    requested_end: datetime

    job_id: UUID = field(default_factory=uuid4)

    instrument_symbol: str | None = None
    timeframe: Timeframe | None = None

    status: IngestionJobStatus = IngestionJobStatus.PENDING

    records_downloaded: int = 0
    records_written: int = 0
    records_rejected: int = 0

    produced_dataset_version_ids: tuple[UUID, ...] = ()

    failure_code: str | None = None
    failure_message: str | None = None

    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate and normalize ingestion job metadata."""

        if not isinstance(self.job_id, UUID):
            raise TypeError("job_id must be a UUID.")

        if not isinstance(self.provider_id, str):
            raise TypeError("provider_id must be a string.")

        provider_id = self.provider_id.strip().lower()

        if not provider_id:
            raise ValueError("provider_id cannot be empty.")

        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", provider_id):
            raise ValueError("provider_id has an invalid format.")

        object.__setattr__(self, "provider_id", provider_id)

        if not isinstance(self.asset_class, AssetClass):
            raise TypeError("asset_class must be an AssetClass value.")

        if not isinstance(self.status, IngestionJobStatus):
            raise TypeError(
                "status must be an IngestionJobStatus value."
            )

        if self.instrument_symbol is not None:
            if not isinstance(self.instrument_symbol, str):
                raise TypeError(
                    "instrument_symbol must be a string or None."
                )

            symbol = self.instrument_symbol.strip().upper()

            if not symbol:
                raise ValueError("instrument_symbol cannot be empty.")

            object.__setattr__(self, "instrument_symbol", symbol)

        if self.timeframe is not None:
            if not isinstance(self.timeframe, Timeframe):
                raise TypeError(
                    "timeframe must be a Timeframe value or None."
                )

        for field_name in (
            "records_downloaded",
            "records_written",
            "records_rejected",
        ):
            value = getattr(self, field_name)

            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer.")

            if value < 0:
                raise ValueError(f"{field_name} cannot be negative.")

        if not isinstance(self.produced_dataset_version_ids, tuple):
            raise TypeError(
                "produced_dataset_version_ids must be a tuple."
            )

        for version_id in self.produced_dataset_version_ids:
            if not isinstance(version_id, UUID):
                raise TypeError(
                    "Every produced dataset version ID must be a UUID."
                )

        if len(set(self.produced_dataset_version_ids)) != len(
            self.produced_dataset_version_ids
        ):
            raise ValueError(
                "Produced dataset version IDs must be unique."
            )

        self._validate_optional_text("failure_code", self.failure_code)
        self._validate_optional_text(
            "failure_message",
            self.failure_message,
        )

        for field_name in (
            "requested_start",
            "requested_end",
            "created_at",
            "started_at",
            "completed_at",
        ):
            value = getattr(self, field_name)

            if value is None:
                if field_name in (
                    "requested_start",
                    "requested_end",
                    "created_at",
                ):
                    raise ValueError(f"{field_name} is required.")
                continue

            normalized = self._normalize_timestamp(field_name, value)
            object.__setattr__(self, field_name, normalized)

        if self.requested_start >= self.requested_end:
            raise ValueError(
                "requested_start must be before requested_end."
            )


        if (
            self.started_at is not None
            and self.started_at < self.created_at
        ):
            raise ValueError("started_at cannot be before created_at.")

        if (
            self.completed_at is not None
            and self.completed_at < self.created_at
        ):
            raise ValueError("completed_at cannot be before created_at.")

        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError(
                "completed_at cannot be before started_at."
            )

        self._validate_lifecycle()

    def _validate_lifecycle(self) -> None:
        """Enforce status-specific lifecycle requirements."""

        if self.status == IngestionJobStatus.PENDING:
            if self.started_at is not None or self.completed_at is not None:
                raise ValueError(
                    "Pending jobs cannot have start or completion timestamps."
                )

            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError(
                    "Pending jobs cannot have failure details."
                )

        elif self.status == IngestionJobStatus.RUNNING:
            if self.started_at is None:
                raise ValueError("Running jobs require started_at.")

            if self.completed_at is not None:
                raise ValueError(
                    "Running jobs cannot have completed_at."
                )

            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError(
                    "Running jobs cannot have failure details."
                )

        elif self.status in TERMINAL_STATUSES:
            if self.completed_at is None:
                raise ValueError(
                    "Terminal jobs require completed_at."
                )

        if self.status in (
            IngestionJobStatus.FAILED,
            IngestionJobStatus.PARTIALLY_SUCCEEDED,
        ):
            if self.failure_message is None:
                raise ValueError(
                    "Failed and partially successful jobs require failure_message."
                )

        elif self.failure_code is not None or self.failure_message is not None:
            raise ValueError(
                "Failure details are only permitted for failed "
                "or partially successful jobs."
            )

        if self.status == IngestionJobStatus.PARTIALLY_SUCCEEDED:
            if not self.produced_dataset_version_ids:
                raise ValueError(
                    "Partially successful jobs require at least one "
                    "produced dataset version."
                )

    @staticmethod
    def _normalize_timestamp(field_name: str, value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError(f"{field_name} must be a datetime value.")

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"{field_name} must include timezone information."
            )

        return value.astimezone(timezone.utc)

    def _validate_optional_text(
        self,
        field_name: str,
        value: str | None,
    ) -> None:
        if value is None:
            return

        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string or None.")

        normalized = value.strip()

        if not normalized:
            raise ValueError(f"{field_name} cannot be empty.")

        object.__setattr__(self, field_name, normalized)