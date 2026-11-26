import asyncio
from functools import wraps
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.raw_dataset_publication import (
    RawDatasetPublicationKindError,
    RawDatasetPublicationService,
    RawDatasetPublicationStorageError,
)
from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
    DatasetKind,
    DatasetVersion,
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)


def async_test(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def make_raw_dataset(*, name="EURUSD Dukascopy raw data"):
    return Dataset(
        name=name,
        data_kind=DatasetKind.RAW_PROVIDER,
        asset_class=AssetClass.FOREX,
        instrument_id=uuid4(),
        instrument_symbol="EURUSD",
        provider_id="dukascopy",
    )


def make_normalized_dataset():
    return Dataset(
        name="EURUSD normalized data",
        data_kind=DatasetKind.OHLCV,
        asset_class=AssetClass.FOREX,
        instrument_id=uuid4(),
        instrument_symbol="EURUSD",
        timeframe=Timeframe.M1,
    )


def make_raw_metadata(
    *,
    storage_format="bi5",
    size_bytes=128,
    record_count=None,
    first_timestamp=None,
    last_timestamp=None,
):
    return StorageObjectMetadata(
        storage_uri="raw_provider/test-payload.bin",
        storage_format=storage_format,
        checksum_algorithm="sha256",
        checksum="a" * 64,
        size_bytes=size_bytes,
        record_count=record_count,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
    )


class FakeDatasetRepository:
    def __init__(self, canonical_dataset=None, *, created=True):
        self.canonical_dataset = canonical_dataset
        self.created = created
        self.calls = []
        self.error = None

    async def get_or_create_raw(self, dataset):
        self.calls.append(dataset)

        if self.error is not None:
            raise self.error

        if self.canonical_dataset is None:
            self.canonical_dataset = dataset

        return self.canonical_dataset, self.created


class FakeLifecycleRepository:
    def __init__(self):
        self.created_versions = []
        self.published_versions = []
        self.failed_versions = []
        self.publish_error = None
        self.create_error = None

    async def create_next_staging_version(
        self,
        dataset_id,
        *,
        schema_version,
        storage_format,
    ):
        if self.create_error is not None:
            raise self.create_error

        version = DatasetVersion(
            dataset_id=dataset_id,
            version_number=1,
            schema_version=schema_version,
            storage_format=storage_format,
            record_count=None,
        )

        self.created_versions.append(version)
        return version

    async def publish_staging_version(
        self,
        dataset_version_id,
        metadata,
    ):
        self.published_versions.append(
            (dataset_version_id, metadata)
        )

        if self.publish_error is not None:
            raise self.publish_error

        staging = self.created_versions[-1]

        return DatasetVersion(
            dataset_id=staging.dataset_id,
            dataset_version_id=staging.dataset_version_id,
            version_number=staging.version_number,
            schema_version=staging.schema_version,
            storage_format=staging.storage_format,
            status=DatasetVersionStatus.AVAILABLE,
            record_count=None,
            size_bytes=metadata.size_bytes,
            storage_uri=metadata.storage_uri,
            checksum_algorithm=metadata.checksum_algorithm,
            checksum=metadata.checksum,
            created_at=staging.created_at,
            published_at=datetime.now(timezone.utc),
        )

    async def fail_staging_version(self, dataset_version_id):
        self.failed_versions.append(dataset_version_id)
        return self.created_versions[-1]


class FakeRawStorage:
    def __init__(
        self,
        *,
        metadata=None,
        write_error=None,
        verify_error=None,
    ):
        self.metadata = metadata or make_raw_metadata()
        self.write_error = write_error
        self.verify_error = verify_error
        self.write_calls = []
        self.verify_calls = []

    async def write(
        self,
        dataset_version_id,
        source,
        storage_format,
    ):
        if self.write_error is not None:
            raise self.write_error

        payload = source.read()

        self.write_calls.append(
            (dataset_version_id, payload, storage_format)
        )

        return self.metadata

    async def verify(self, dataset_version_id, expected):
        self.verify_calls.append((dataset_version_id, expected))

        if self.verify_error is not None:
            raise self.verify_error

    def read(self, dataset_version_id):
        raise NotImplementedError


def make_service(
    *,
    candidate=None,
    canonical=None,
    created=True,
    storage=None,
):
    candidate = candidate or make_raw_dataset()
    canonical = canonical or candidate

    datasets = FakeDatasetRepository(
        canonical,
        created=created,
    )
    lifecycle = FakeLifecycleRepository()
    raw_storage = storage or FakeRawStorage()

    service = RawDatasetPublicationService(
        datasets,
        lifecycle,
        raw_storage,
    )

    return service, datasets, lifecycle, raw_storage, candidate


@async_test
async def test_publish_raw_payload_writes_verifies_and_publishes():
    service, datasets, lifecycle, storage, dataset = make_service()
    source = BytesIO(b"original provider payload")

    result = await service.publish(
        dataset,
        source,
        storage_format="BI5",
    )

    assert result.dataset == dataset
    assert result.dataset_created is True
    assert result.dataset_version.status == DatasetVersionStatus.AVAILABLE
    assert result.dataset_version.record_count is None
    assert result.dataset_version.first_timestamp is None
    assert result.dataset_version.last_timestamp is None
    assert result.dataset_version.size_bytes == 128

    assert datasets.calls == [dataset]
    assert len(lifecycle.created_versions) == 1
    assert len(storage.write_calls) == 1
    assert storage.write_calls[0][1] == b"original provider payload"
    assert storage.write_calls[0][2] == "bi5"
    assert len(storage.verify_calls) == 1
    assert len(lifecycle.published_versions) == 1
    assert lifecycle.failed_versions == []

    assert not source.closed


@async_test
async def test_publish_uses_canonical_dataset_returned_by_repository():
    candidate = make_raw_dataset(name="Candidate dataset")
    canonical = make_raw_dataset(name="Canonical dataset")

    service, _, _, _, _ = make_service(
        candidate=candidate,
        canonical=canonical,
        created=False,
    )

    result = await service.publish(
        candidate,
        BytesIO(b"provider payload"),
        storage_format="bi5",
    )

    assert result.dataset == canonical
    assert result.dataset_created is False


@async_test
async def test_non_raw_dataset_is_rejected_before_repository_access():
    service, datasets, lifecycle, storage, _ = make_service()
    normalized = make_normalized_dataset()

    with pytest.raises(RawDatasetPublicationKindError):
        await service.publish(
            normalized,
            BytesIO(b"payload"),
            storage_format="parquet",
        )

    assert datasets.calls == []
    assert lifecycle.created_versions == []
    assert storage.write_calls == []


@async_test
async def test_invalid_storage_format_is_rejected_before_repository_access():
    service, datasets, lifecycle, storage, dataset = make_service()

    with pytest.raises(ValueError, match="storage_format"):
        await service.publish(
            dataset,
            BytesIO(b"payload"),
            storage_format=" ",
        )

    assert datasets.calls == []
    assert lifecycle.created_versions == []
    assert storage.write_calls == []


@pytest.mark.parametrize(
    "metadata",
    [
        make_raw_metadata(size_bytes=0),
        make_raw_metadata(storage_format="csv"),
        make_raw_metadata(record_count=1),
        make_raw_metadata(
            record_count=1,
            first_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ],
)
@async_test
async def test_invalid_raw_metadata_fails_staging(metadata):
    storage = FakeRawStorage(metadata=metadata)
    service, _, lifecycle, _, dataset = make_service(storage=storage)

    with pytest.raises(RawDatasetPublicationStorageError):
        await service.publish(
            dataset,
            BytesIO(b"provider payload"),
            storage_format="bi5",
        )

    assert len(lifecycle.created_versions) == 1
    assert lifecycle.failed_versions == [
        lifecycle.created_versions[0].dataset_version_id
    ]
    assert storage.verify_calls == []
    assert lifecycle.published_versions == []


@async_test
async def test_empty_payload_is_rejected_and_staging_is_failed():
    storage = FakeRawStorage(
        metadata=make_raw_metadata(size_bytes=0)
    )
    service, _, lifecycle, _, dataset = make_service(storage=storage)

    with pytest.raises(
        RawDatasetPublicationStorageError,
        match="must not be empty",
    ):
        await service.publish(
            dataset,
            BytesIO(b""),
            storage_format="bi5",
        )

    assert len(lifecycle.failed_versions) == 1
    assert storage.verify_calls == []
    assert lifecycle.published_versions == []


@async_test
async def test_storage_write_failure_marks_staging_failed():
    storage = FakeRawStorage(
        write_error=RuntimeError("raw storage write failed")
    )
    service, _, lifecycle, _, dataset = make_service(storage=storage)

    with pytest.raises(
        RuntimeError,
        match="raw storage write failed",
    ):
        await service.publish(
            dataset,
            BytesIO(b"provider payload"),
            storage_format="bi5",
        )

    assert len(lifecycle.created_versions) == 1
    assert len(lifecycle.failed_versions) == 1
    assert storage.verify_calls == []
    assert lifecycle.published_versions == []


@async_test
async def test_verification_failure_marks_staging_failed():
    storage = FakeRawStorage(
        verify_error=RuntimeError("raw checksum mismatch")
    )
    service, _, lifecycle, _, dataset = make_service(storage=storage)

    with pytest.raises(
        RuntimeError,
        match="raw checksum mismatch",
    ):
        await service.publish(
            dataset,
            BytesIO(b"provider payload"),
            storage_format="bi5",
        )

    assert len(storage.write_calls) == 1
    assert len(storage.verify_calls) == 1
    assert len(lifecycle.failed_versions) == 1
    assert lifecycle.published_versions == []


@async_test
async def test_publication_failure_attempts_to_fail_staging():
    lifecycle_error = RuntimeError("database publication failed")
    service, _, lifecycle, _, dataset = make_service()
    lifecycle.publish_error = lifecycle_error

    with pytest.raises(
        RuntimeError,
        match="database publication failed",
    ):
        await service.publish(
            dataset,
            BytesIO(b"provider payload"),
            storage_format="bi5",
        )

    assert len(lifecycle.published_versions) == 1
    assert len(lifecycle.failed_versions) == 1


@async_test
async def test_cancellation_leaves_staging_for_recovery():
    storage = FakeRawStorage()
    service, _, lifecycle, _, dataset = make_service(storage=storage)

    async def cancel_write(
        dataset_version_id,
        source,
        storage_format,
    ):
        raise asyncio.CancelledError()

    storage.write = cancel_write

    with pytest.raises(asyncio.CancelledError):
        await service.publish(
            dataset,
            BytesIO(b"provider payload"),
            storage_format="bi5",
        )

    assert len(lifecycle.created_versions) == 1
    assert lifecycle.failed_versions == []
    assert lifecycle.published_versions == []


@async_test
async def test_repository_failure_does_not_allocate_staging_version():
    service, datasets, lifecycle, storage, dataset = make_service()
    datasets.error = RuntimeError("dataset repository unavailable")

    with pytest.raises(
        RuntimeError,
        match="dataset repository unavailable",
    ):
        await service.publish(
            dataset,
            BytesIO(b"provider payload"),
            storage_format="bi5",
        )

    assert len(datasets.calls) == 1
    assert lifecycle.created_versions == []
    assert storage.write_calls == []


@async_test
async def test_staging_allocation_failure_does_not_write_payload():
    service, _, lifecycle, storage, dataset = make_service()
    lifecycle.create_error = RuntimeError("version allocation failed")

    with pytest.raises(
        RuntimeError,
        match="version allocation failed",
    ):
        await service.publish(
            dataset,
            BytesIO(b"provider payload"),
            storage_format="bi5",
        )

    assert lifecycle.created_versions == []
    assert storage.write_calls == []