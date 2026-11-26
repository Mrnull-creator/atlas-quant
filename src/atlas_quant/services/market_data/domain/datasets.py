"""Domain models for historical market data datasets."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)


class DatasetKind(StrEnum):
    """Identifies the type of data held by a dataset."""

    OHLCV = "ohlcv"
    BID_ASK = "bid_ask"
    RAW_PROVIDER = "raw_provider"


class DatasetVersionStatus(StrEnum):
    """Lifecycle states of a dataset version."""

    STAGING = "staging"
    VERIFIED = "verified"
    AVAILABLE = "available"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class Dataset:
    """Represents the stable identity of a logical market data collection.

    Dataset versions contain the actual immutable data snapshots.
    """

    name: str
    data_kind: DatasetKind
    asset_class: AssetClass

    dataset_id: UUID = field(default_factory=uuid4)
    instrument_id: UUID | None = None

    instrument_symbol: str | None = None
    provider_id: str | None = None
    timeframe: Timeframe | None = None

    latest_version_id: UUID | None = None

    def __post_init__(self) -> None:
        """Validate and normalize dataset identity."""

        if not isinstance(self.dataset_id, UUID):
            raise TypeError("dataset_id must be a UUID.")

        if self.instrument_id is not None and not isinstance(
            self.instrument_id, UUID
        ):
            raise TypeError("instrument_id must be a UUID or None.")

        if not isinstance(self.name, str):
            raise TypeError("name must be a string.")

        normalized_name = self.name.strip()

        if not normalized_name:
            raise ValueError("name cannot be empty.")

        object.__setattr__(self, "name", normalized_name)

        if not isinstance(self.data_kind, DatasetKind):
            raise TypeError("data_kind must be a DatasetKind value.")

        if not isinstance(self.asset_class, AssetClass):
            raise TypeError("asset_class must be an AssetClass value.")

        if self.instrument_symbol is not None:
            if not isinstance(self.instrument_symbol, str):
                raise TypeError("instrument_symbol must be a string.")

            symbol = self.instrument_symbol.strip().upper()

            if not symbol:
                raise ValueError("instrument_symbol cannot be empty.")

            object.__setattr__(self, "instrument_symbol", symbol)

        if self.provider_id is not None:
            if not isinstance(self.provider_id, str):
                raise TypeError("provider_id must be a string.")

            provider_id = self.provider_id.strip().lower()

            if not provider_id:
                raise ValueError("provider_id cannot be empty.")

            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", provider_id):
                raise ValueError("provider_id has an invalid format.")

            object.__setattr__(self, "provider_id", provider_id)

        if self.timeframe is not None:
            if not isinstance(self.timeframe, Timeframe):
                raise TypeError("timeframe must be a Timeframe value.")

        if self.latest_version_id is not None:
            if not isinstance(self.latest_version_id, UUID):
                raise TypeError("latest_version_id must be a UUID.")

        if self.data_kind in (DatasetKind.OHLCV, DatasetKind.BID_ASK):
            if self.instrument_id is None:
                raise ValueError(
                    "Normalized datasets require an instrument_id."
                )

        if self.data_kind == DatasetKind.RAW_PROVIDER:
            if self.provider_id is None:
                raise ValueError(
                    "Raw provider datasets require a provider_id."
                )

        elif self.data_kind == DatasetKind.OHLCV:
            if self.instrument_symbol is None:
                raise ValueError(
                    "Normalized datasets require an instrument_symbol."
                )

            if self.timeframe is None:
                raise ValueError("OHLCV datasets require a timeframe.")

        elif self.data_kind == DatasetKind.BID_ASK:
            if self.instrument_symbol is None:
                raise ValueError(
                    "Normalized datasets require an instrument_symbol."
                )

            if self.timeframe is not None:
                raise ValueError(
                    "Bid/ask datasets must not have a timeframe."
                )


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    """Represents an immutable snapshot of a logical dataset.

    A normalized version has a record count and observation boundaries.

    An opaque raw-provider version may use record_count=None because its
    payload has not necessarily been parsed into market observations.

    The lifecycle is:

        STAGING -> VERIFIED -> AVAILABLE

    A version may instead become FAILED or ABANDONED.

    AVAILABLE is retained as the persisted publication state for
    compatibility with existing dataset versions. In the recovery
    architecture, AVAILABLE represents a published version.

    verified_at and ingestion_attempt_id are optional to preserve
    compatibility with historical versions and versions created by
    workflows that are not associated with an ingestion attempt.

    size_bytes is nullable to support historical versions created before
    physical object size was persisted.
    """

    dataset_id: UUID
    version_number: int

    schema_version: str
    storage_format: str

    status: DatasetVersionStatus = DatasetVersionStatus.STAGING

    dataset_version_id: UUID = field(default_factory=uuid4)

    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None

    record_count: int | None = 0
    size_bytes: int | None = None

    storage_uri: str | None = None

    checksum_algorithm: str | None = None
    checksum: str | None = None

    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    published_at: datetime | None = None

    # Recovery-model fields are appended to preserve positional callers.
    verified_at: datetime | None = None
    ingestion_attempt_id: UUID | None = None

    def __post_init__(self) -> None:
        """Validate dataset version metadata."""

        if not isinstance(self.dataset_id, UUID):
            raise TypeError("dataset_id must be a UUID.")

        if not isinstance(self.dataset_version_id, UUID):
            raise TypeError("dataset_version_id must be a UUID.")

        if self.ingestion_attempt_id is not None and not isinstance(
            self.ingestion_attempt_id, UUID
        ):
            raise TypeError(
                "ingestion_attempt_id must be a UUID or None."
            )

        if (
            isinstance(self.version_number, bool)
            or not isinstance(self.version_number, int)
        ):
            raise TypeError("version_number must be an integer.")

        if self.version_number < 1:
            raise ValueError("version_number must be at least 1.")

        if not isinstance(self.status, DatasetVersionStatus):
            raise TypeError(
                "status must be a DatasetVersionStatus value."
            )

        self._validate_text("schema_version", self.schema_version)
        self._validate_text("storage_format", self.storage_format)

        object.__setattr__(
            self,
            "schema_version",
            self.schema_version.strip(),
        )
        object.__setattr__(
            self,
            "storage_format",
            self.storage_format.strip().lower(),
        )

        self._validate_counts()

        self._validate_timestamps()

        self._validate_observation_boundaries()

        self._validate_storage_metadata()

        self._validate_lifecycle()
    def _validate_counts(self) -> None:
        """Validate record count and physical storage size."""

        if self.record_count is not None:
            if (
                isinstance(self.record_count, bool)
                or not isinstance(self.record_count, int)
            ):
                raise TypeError(
                    "record_count must be an integer or None."
                )

            if self.record_count < 0:
                raise ValueError("record_count cannot be negative.")

        if self.size_bytes is not None:
            if (
                isinstance(self.size_bytes, bool)
                or not isinstance(self.size_bytes, int)
            ):
                raise TypeError(
                    "size_bytes must be an integer or None."
                )

            if self.size_bytes < 0:
                raise ValueError("size_bytes cannot be negative.")

    def _validate_timestamps(self) -> None:
        """Validate and normalize lifecycle timestamps."""

        self._validate_timestamp("created_at", self.created_at)

        for field_name in (
            "first_timestamp",
            "last_timestamp",
            "verified_at",
            "published_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                self._validate_timestamp(field_name, value)

        if (
            self.verified_at is not None
            and self.verified_at < self.created_at
        ):
            raise ValueError(
                "verified_at cannot be before created_at."
            )

        if (
            self.published_at is not None
            and self.published_at < self.created_at
        ):
            raise ValueError(
                "published_at cannot be before created_at."
            )

        if (
            self.verified_at is not None
            and self.published_at is not None
            and self.verified_at > self.published_at
        ):
            raise ValueError(
                "verified_at cannot be after published_at."
            )

    def _validate_observation_boundaries(self) -> None:
        """Validate first and last observation timestamps."""

        if self.record_count is None or self.record_count == 0:
            if (
                self.first_timestamp is not None
                or self.last_timestamp is not None
            ):
                raise ValueError(
                    "Empty versions cannot have observation boundaries."
                )
        else:
            if (
                self.first_timestamp is None
                or self.last_timestamp is None
            ):
                raise ValueError(
                    "Versions containing observations require both "
                    "observation boundaries."
                )

            if self.first_timestamp > self.last_timestamp:
                raise ValueError(
                    "first_timestamp cannot be after last_timestamp."
                )

    def _validate_storage_metadata(self) -> None:
        """Validate and normalize optional storage metadata."""

        self._validate_optional_text("storage_uri", self.storage_uri)

        self._validate_optional_text(
            "checksum_algorithm",
            self.checksum_algorithm,
        )
        self._validate_optional_text("checksum", self.checksum)

        if (self.checksum_algorithm is None) != (self.checksum is None):
            raise ValueError(
                "checksum_algorithm and checksum must be provided together."
            )

        if self.checksum_algorithm is not None:
            object.__setattr__(
                self,
                "checksum_algorithm",
                self.checksum_algorithm.lower(),
            )

    def _validate_lifecycle(self) -> None:
        """Enforce status-specific version lifecycle requirements."""

        if self.status == DatasetVersionStatus.STAGING:
            if self.verified_at is not None:
                raise ValueError(
                    "Staging versions cannot have verified_at."
                )

            if self.published_at is not None:
                raise ValueError(
                    "Only available versions can have published_at."
                )

        elif self.status == DatasetVersionStatus.VERIFIED:
            if self.published_at is not None:
                raise ValueError(
                    "Verified versions cannot have published_at."
                )

            if self.verified_at is None:
                raise ValueError(
                    "Verified versions require verified_at."
                )

            self._validate_storage_requirements(
                "Verified versions"
            )

        elif self.status == DatasetVersionStatus.AVAILABLE:
            self._validate_storage_requirements(
                "Available versions"
            )
            self._validate_publication_requirements()

            # verified_at intentionally remains optional for historical
            # AVAILABLE versions created before this lifecycle extension.

        elif self.status == DatasetVersionStatus.FAILED:
            if self.verified_at is not None:
                raise ValueError(
                    "Failed versions cannot have verified_at."
                )

            if self.published_at is not None:
                raise ValueError(
                    "Only available versions can have published_at."
                )

        elif self.status == DatasetVersionStatus.ABANDONED:
            if self.published_at is not None:
                raise ValueError(
                    "Abandoned versions cannot have published_at."
                )

            # An abandoned version may have been verified before the
            # execution that owned it was abandoned.

    def _validate_storage_requirements(
        self,
        lifecycle_name: str,
    ) -> None:
        """Require complete storage metadata before verification/publication.

        record_count=None is intentionally permitted for opaque
        raw-provider payloads. A known record_count of zero is not
        permitted for verified or published versions.
        """

        if self.record_count == 0:
            raise ValueError(
                f"{lifecycle_name} must contain at least one record."
            )

        if self.storage_uri is None:
            raise ValueError(
                f"{lifecycle_name} require a storage_uri."
            )

        if (
            self.checksum_algorithm is None
            or self.checksum is None
        ):
            raise ValueError(
                f"{lifecycle_name} require checksum metadata."
            )

    def _validate_publication_requirements(self) -> None:
        """Require complete storage metadata before publication."""

        if self.record_count == 0:
            raise ValueError(
                "Available versions must contain at least one record."
            )

        if self.storage_uri is None:
            raise ValueError(
                "Available versions require a storage_uri."
            )

        if self.checksum_algorithm is None or self.checksum is None:
            raise ValueError(
                "Available versions require checksum metadata."
            )

        if self.published_at is None:
            raise ValueError(
                "Available versions require published_at."
            )

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
    ) -> None:
        """Validate a required non-empty text value."""

        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string.")

        if not value.strip():
            raise ValueError(f"{field_name} cannot be empty.")

    def _validate_optional_text(
        self,
        field_name: str,
        value: str | None,
    ) -> None:
        """Validate and normalize an optional text value."""

        if value is None:
            return

        if not isinstance(value, str):
            raise TypeError(
                f"{field_name} must be a string or None."
            )

        normalized = value.strip()

        if not normalized:
            raise ValueError(f"{field_name} cannot be empty.")

        object.__setattr__(
            self,
            field_name,
            normalized,
        )

    def _validate_timestamp(
        self,
        field_name: str,
        value: datetime,
    ) -> None:
        """Validate a timezone-aware timestamp and normalize it to UTC."""

        if not isinstance(value, datetime):
            raise TypeError(
                f"{field_name} must be a datetime value."
            )

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"{field_name} must include timezone information."
            )

        object.__setattr__(
            self,
            field_name,
            value.astimezone(timezone.utc),
        )