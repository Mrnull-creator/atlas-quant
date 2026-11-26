"""Application service for publishing immutable raw provider datasets."""

from dataclasses import dataclass
from typing import BinaryIO
from uuid import UUID

from atlas_quant.services.market_data.application.dataset_lifecycle import (
    DatasetLifecycleRepository,
)
from atlas_quant.services.market_data.application.repositories import (
    DatasetRepository,
)
from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
    VerifiableRawDataStorage,
)
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
    DatasetKind,
    DatasetVersion,
)
from atlas_quant.services.market_data.domain.ingestion_attempt import IngestionAttempt


class RawDatasetPublicationError(Exception):
    """Base exception for raw dataset publication failures."""


class RawDatasetPublicationKindError(RawDatasetPublicationError):
    """Raised when the supplied dataset is not a raw provider dataset."""


class RawDatasetPublicationStorageError(RawDatasetPublicationError):
    """Raised when raw storage returns incompatible metadata."""


@dataclass(frozen=True, slots=True)
class RawDatasetPublicationResult:
    """Result of successfully publishing an immutable raw dataset version."""

    dataset: Dataset
    dataset_created: bool
    dataset_version: DatasetVersion


class RawDatasetPublicationService:
    """Coordinate raw dataset registration, storage, verification and publication."""

    def __init__(
        self,
        dataset_repository: DatasetRepository,
        lifecycle_repository: DatasetLifecycleRepository,
        raw_storage: VerifiableRawDataStorage,
        *,
        schema_version: str = "1",
    ) -> None:
        if not isinstance(schema_version, str) or not schema_version.strip():
            raise ValueError("schema_version must be a non-empty string.")

        self._datasets = dataset_repository
        self._lifecycle = lifecycle_repository
        self._storage = raw_storage
        self._schema_version = schema_version.strip()

    async def publish(
        self,
        dataset: Dataset,
        source: BinaryIO,
        *,
        storage_format: str,
        attempt: IngestionAttempt | None = None,
    ) -> RawDatasetPublicationResult:
        """Preserve and publish an original provider payload."""

        if not isinstance(dataset, Dataset):
            raise TypeError("dataset must be a Dataset.")

        if dataset.data_kind != DatasetKind.RAW_PROVIDER:
            raise RawDatasetPublicationKindError(
                "Raw publication requires a RAW_PROVIDER dataset."
            )

        if not isinstance(storage_format, str) or not storage_format.strip():
            raise ValueError("storage_format must be a non-empty string.")

        normalized_format = storage_format.strip().lower()

        canonical_dataset, dataset_created = (
            await self._datasets.get_or_create_raw(dataset)
        )

        version = await self._lifecycle.create_next_staging_version(
            canonical_dataset.dataset_id,
            schema_version=self._schema_version,
            storage_format=normalized_format,
            **({"attempt": attempt} if attempt is not None else {}),
        )

        return await self._write_verify_publish(
            canonical_dataset,
            dataset_created,
            version,
            source,
            normalized_format,
            attempt=attempt,
        )

    async def _write_verify_publish(
        self,
        dataset: Dataset,
        dataset_created: bool,
        version: DatasetVersion,
        source: BinaryIO,
        storage_format: str,
        *,
        attempt: IngestionAttempt | None = None,
    ) -> RawDatasetPublicationResult:
        """Write, verify and publish a staged raw dataset version."""

        try:
            metadata = await self._storage.write(
                version.dataset_version_id,
                source,
                storage_format,
            )

            self._validate_metadata(metadata, version.storage_format)

            await self._storage.verify(
                version.dataset_version_id,
                metadata,
            )

            published_version = await self._lifecycle.publish_staging_version(
                version.dataset_version_id,
                metadata,
                **({"attempt": attempt} if attempt is not None else {}),
            )

            return RawDatasetPublicationResult(
                dataset=dataset,
                dataset_created=dataset_created,
                dataset_version=published_version,
            )

        except Exception as operation_error:
            try:
                await self._lifecycle.fail_staging_version(
                    version.dataset_version_id,
                    **({"attempt": attempt} if attempt is not None else {}),
                )
            except Exception as failure_error:
                operation_error.add_note(
                    "Additionally, the staging version could not be "
                    f"marked FAILED: {failure_error!r}"
                )

            raise

    @staticmethod
    def _validate_metadata(
        metadata: StorageObjectMetadata,
        expected_storage_format: str,
    ) -> None:
        """Ensure metadata describes a non-empty opaque raw payload."""

        if not isinstance(metadata, StorageObjectMetadata):
            raise RawDatasetPublicationStorageError(
                "Raw storage returned invalid metadata."
            )

        if metadata.storage_format != expected_storage_format:
            raise RawDatasetPublicationStorageError(
                "Raw storage format does not match the allocated dataset version."
            )

        if metadata.size_bytes <= 0:
            raise RawDatasetPublicationStorageError(
                "A published raw provider payload must not be empty."
            )

        if metadata.record_count is not None:
            raise RawDatasetPublicationStorageError(
                "Raw provider payload metadata must not contain a record count."
            )

        if (
            metadata.first_timestamp is not None
            or metadata.last_timestamp is not None
        ):
            raise RawDatasetPublicationStorageError(
                "Raw provider payload metadata must not contain "
                "observation timestamp boundaries."
            )