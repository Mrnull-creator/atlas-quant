"""Storage interfaces for historical market data.

These protocols define technology-independent storage operations.

Implementations belong in the infrastructure layer. The application
layer must not depend on a particular filesystem, object store,
database, or columnar storage format.
"""

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO, Protocol
from uuid import UUID

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


@dataclass(frozen=True, slots=True)
class StorageObjectMetadata:
    """Describes a stored dataset-version object."""

    storage_uri: str
    storage_format: str
    checksum_algorithm: str
    checksum: str
    size_bytes: int

    record_count: int | None = None
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        """Validate storage metadata."""

        for field_name in (
            "storage_uri",
            "storage_format",
            "checksum_algorithm",
            "checksum",
        ):
            value = getattr(self, field_name)

            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string.")

            normalized = value.strip()

            if not normalized:
                raise ValueError(f"{field_name} cannot be empty.")

            object.__setattr__(self, field_name, normalized)

        object.__setattr__(
            self,
            "storage_format",
            self.storage_format.lower(),
        )
        object.__setattr__(
            self,
            "checksum_algorithm",
            self.checksum_algorithm.lower(),
        )

        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
        ):
            raise TypeError("size_bytes must be an integer.")

        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative.")

        if self.record_count is not None:
            if (
                isinstance(self.record_count, bool)
                or not isinstance(self.record_count, int)
            ):
                raise TypeError("record_count must be an integer or None.")

            if self.record_count < 0:
                raise ValueError("record_count cannot be negative.")

        self._normalize_timestamp("first_timestamp")
        self._normalize_timestamp("last_timestamp")

        if (self.first_timestamp is None) != (
            self.last_timestamp is None
        ):
            raise ValueError(
                "first_timestamp and last_timestamp must be provided "
                "together."
            )

        if (
            self.first_timestamp is not None
            and self.last_timestamp is not None
            and self.first_timestamp > self.last_timestamp
        ):
            raise ValueError(
                "first_timestamp cannot be after last_timestamp."
            )

        if self.record_count == 0 and self.first_timestamp is not None:
            raise ValueError(
                "Empty storage objects cannot have observation boundaries."
            )

    def _normalize_timestamp(self, field_name: str) -> None:
        """Require an aware timestamp and normalize it to UTC."""

        value = getattr(self, field_name)

        if value is None:
            return

        if not isinstance(value, datetime):
            raise TypeError(
                f"{field_name} must be a datetime value or None."
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


class OHLCVStorage(Protocol):
    """Storage interface for normalized OHLCV observations."""

    async def write(
        self,
        dataset_version_id: UUID,
        bars: Iterable[OHLCVBar],
    ) -> StorageObjectMetadata:
        """Store OHLCV observations for a dataset version.

        Implementations must not overwrite an existing dataset version.
        """
        ...

    def read(
        self,
        dataset_version_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[OHLCVBar]:
        """Read OHLCV observations in the requested time range.

        The start boundary is inclusive.
        The end boundary is exclusive.
        Returned timestamps are UTC.
        """
        ...


class QuoteStorage(Protocol):
    """Storage interface for normalized bid/ask observations."""

    async def write(
        self,
        dataset_version_id: UUID,
        quotes: Iterable[BidAskQuote],
    ) -> StorageObjectMetadata:
        """Store bid/ask observations for a dataset version.

        Implementations must not overwrite an existing dataset version.
        """
        ...

    def read(
        self,
        dataset_version_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[BidAskQuote]:
        """Read quote observations in the requested time range.

        The start boundary is inclusive.
        The end boundary is exclusive.
        Returned timestamps are UTC.
        """
        ...


class RawDataStorage(Protocol):
    """Storage interface for original provider data."""

    async def write(
        self,
        dataset_version_id: UUID,
        source: BinaryIO,
        storage_format: str,
    ) -> StorageObjectMetadata:
        """Preserve an original provider payload without normalization.

        Implementations must not overwrite an existing dataset version.
        The source is a binary stream and should be consumed incrementally.
        """
        ...

    def read(
        self,
        dataset_version_id: UUID,
    ) -> BinaryIO:
        """Open the original provider payload as a binary stream.

        The caller is responsible for closing the returned stream.
        """
        ...


class VerifiableRawDataStorage(RawDataStorage, Protocol):
    """Raw storage that can verify an object before publication."""

    async def verify(
        self,
        dataset_version_id: UUID,
        expected: StorageObjectMetadata,
    ) -> None:
        """Verify the stored payload against its expected metadata."""
        ...
