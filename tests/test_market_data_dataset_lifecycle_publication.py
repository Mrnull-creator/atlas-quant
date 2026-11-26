import asyncio
from datetime import datetime, timezone
from functools import wraps
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.dataset_lifecycle_errors import (
    DatasetLifecycleDatasetNotFoundError,
    DatasetLifecycleInvalidPublicationMetadataError,
    DatasetLifecycleInvalidVersionStateError,
    DatasetLifecycleVersionNotFoundError,
)
from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.domain.datasets import (
    DatasetKind,
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_lifecycle_repository import (
    SQLAlchemyDatasetLifecycleRepository,
)


def async_test(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def one_or_none(self):
        if self.value is None:
            return None

        if isinstance(self.value, tuple):
            return self.value

        # Publication identity queries select (dataset_id,
        # ingestion_attempt_id). Existing standalone-version fixtures
        # provide only dataset_id, so represent their attempt ID as None.
        return (self.value, None)


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.flush_count = 0
        self.executed_statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return FakeTransaction()

    async def execute(self, statement):
        self.executed_statements.append(statement)

        if not self.results:
            raise AssertionError("Unexpected database query.")

        return FakeResult(self.results.pop(0))

    async def flush(self):
        self.flush_count += 1


class FakeSessionFactory:
    def __init__(self, session):
        self.session = session
        self.created = False

    def __call__(self):
        self.created = True
        return self.session


def make_metadata(
    *,
    storage_format="parquet",
    record_count=10,
    first_timestamp=None,
    last_timestamp=None,
):
    if record_count is None or record_count == 0:
        first = first_timestamp
        last = last_timestamp
    else:
        first = (
            first_timestamp
            if first_timestamp is not None
            else datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        )
        last = (
            last_timestamp
            if last_timestamp is not None
            else datetime(2026, 1, 1, 0, 9, tzinfo=timezone.utc)
        )

    return StorageObjectMetadata(
        storage_uri="market-data/test/test.parquet",
        storage_format=storage_format,
        checksum_algorithm="sha256",
        checksum="a" * 64,
        size_bytes=1024,
        record_count=record_count,
        first_timestamp=first,
        last_timestamp=last,
    )


def make_dataset(
    *,
    latest_version_id=None,
    data_kind=DatasetKind.OHLCV,
):
    return SimpleNamespace(
        dataset_id=uuid4(),
        latest_version_id=latest_version_id,
        data_kind=data_kind,
    )


def make_version(
    dataset_id,
    *,
    version_number=1,
    status=DatasetVersionStatus.STAGING,
    storage_format="parquet",
):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    return SimpleNamespace(
        dataset_version_id=uuid4(),
        dataset_id=dataset_id,
        version_number=version_number,
        schema_version="1",
        storage_format=storage_format,
        status=status,
        first_timestamp=None,
        last_timestamp=None,
        record_count=0,
        size_bytes=None,
        storage_uri=None,
        checksum_algorithm=None,
        checksum=None,
        created_at=now,
        published_at=None,
    )


def make_repository(results):
    session = FakeSession(results)
    factory = FakeSessionFactory(session)
    repository = SQLAlchemyDatasetLifecycleRepository(factory)
    return repository, session, factory


@async_test
async def test_publish_staging_version_updates_version_and_latest_pointer():
    dataset = make_dataset()
    version = make_version(dataset.dataset_id)
    metadata = make_metadata()

    repository, session, _ = make_repository(
        [dataset.dataset_id, dataset, version]
    )

    published = await repository.publish_staging_version(
        version.dataset_version_id,
        metadata,
    )

    assert published.dataset_version_id == version.dataset_version_id
    assert published.dataset_id == dataset.dataset_id
    assert published.version_number == 1
    assert published.status == DatasetVersionStatus.AVAILABLE
    assert published.storage_uri == metadata.storage_uri
    assert published.storage_format == metadata.storage_format
    assert published.checksum_algorithm == metadata.checksum_algorithm
    assert published.checksum == metadata.checksum
    assert published.record_count == metadata.record_count
    assert published.size_bytes == metadata.size_bytes
    assert published.first_timestamp == metadata.first_timestamp
    assert published.last_timestamp == metadata.last_timestamp
    assert published.published_at is not None

    assert dataset.latest_version_id == version.dataset_version_id
    assert session.flush_count == 1


@async_test
async def test_publish_raw_provider_version_without_observation_metadata():
    dataset = make_dataset(data_kind=DatasetKind.RAW_PROVIDER)
    version = make_version(dataset.dataset_id)
    metadata = make_metadata(record_count=None)

    repository, session, _ = make_repository(
        [dataset.dataset_id, dataset, version]
    )

    published = await repository.publish_staging_version(
        version.dataset_version_id,
        metadata,
    )

    assert published.status == DatasetVersionStatus.AVAILABLE
    assert published.record_count is None
    assert published.first_timestamp is None
    assert published.last_timestamp is None
    assert published.size_bytes == metadata.size_bytes
    assert published.storage_uri == metadata.storage_uri
    assert published.checksum == metadata.checksum
    assert published.published_at is not None

    assert dataset.latest_version_id == version.dataset_version_id
    assert session.flush_count == 1


@async_test
async def test_publish_newer_version_advances_latest_pointer():
    old_latest_id = uuid4()
    dataset = make_dataset(latest_version_id=old_latest_id)
    version = make_version(dataset.dataset_id, version_number=3)

    repository, session, _ = make_repository(
        [dataset.dataset_id, dataset, version, 2]
    )

    published = await repository.publish_staging_version(
        version.dataset_version_id,
        make_metadata(),
    )

    assert published.status == DatasetVersionStatus.AVAILABLE
    assert dataset.latest_version_id == version.dataset_version_id
    assert session.flush_count == 1


@async_test
async def test_publishing_older_version_does_not_regress_latest_pointer():
    current_latest_id = uuid4()
    dataset = make_dataset(latest_version_id=current_latest_id)
    version = make_version(dataset.dataset_id, version_number=2)

    repository, session, _ = make_repository(
        [dataset.dataset_id, dataset, version, 3]
    )

    published = await repository.publish_staging_version(
        version.dataset_version_id,
        make_metadata(),
    )

    assert published.status == DatasetVersionStatus.AVAILABLE
    assert dataset.latest_version_id == current_latest_id
    assert session.flush_count == 1


@async_test
async def test_publish_raises_when_version_does_not_exist():
    repository, session, _ = make_repository([None])

    with pytest.raises(DatasetLifecycleVersionNotFoundError):
        await repository.publish_staging_version(
            uuid4(),
            make_metadata(),
        )

    assert session.flush_count == 0


@async_test
async def test_publish_raises_when_dataset_does_not_exist():
    dataset_id = uuid4()
    repository, session, _ = make_repository(
        [dataset_id, None]
    )

    with pytest.raises(DatasetLifecycleDatasetNotFoundError):
        await repository.publish_staging_version(
            uuid4(),
            make_metadata(),
        )

    assert session.flush_count == 0


@async_test
async def test_publish_raises_when_version_disappears_after_dataset_lock():
    dataset = make_dataset()
    repository, session, _ = make_repository(
        [dataset.dataset_id, dataset, None]
    )

    with pytest.raises(DatasetLifecycleVersionNotFoundError):
        await repository.publish_staging_version(
            uuid4(),
            make_metadata(),
        )

    assert session.flush_count == 0


@async_test
async def test_publish_rejects_version_that_is_not_staging():
    dataset = make_dataset()
    version = make_version(
        dataset.dataset_id,
        status=DatasetVersionStatus.AVAILABLE,
    )

    repository, session, _ = make_repository(
        [dataset.dataset_id, dataset, version]
    )

    with pytest.raises(DatasetLifecycleInvalidVersionStateError):
        await repository.publish_staging_version(
            version.dataset_version_id,
            make_metadata(),
        )

    assert session.flush_count == 0


@async_test
async def test_publish_rejects_storage_format_mismatch():
    dataset = make_dataset()
    version = make_version(dataset.dataset_id)

    repository, session, _ = make_repository(
        [dataset.dataset_id, dataset, version]
    )

    with pytest.raises(DatasetLifecycleInvalidPublicationMetadataError):
        await repository.publish_staging_version(
            version.dataset_version_id,
            make_metadata(storage_format="csv"),
        )

    assert session.flush_count == 0


@async_test
async def test_publish_rejects_zero_record_metadata_before_database_access():
    metadata = make_metadata(record_count=0)

    session = FakeSession([])
    factory = FakeSessionFactory(session)
    repository = SQLAlchemyDatasetLifecycleRepository(factory)

    with pytest.raises(DatasetLifecycleInvalidPublicationMetadataError):
        await repository.publish_staging_version(
            uuid4(),
            metadata,
        )

    assert factory.created is False
    assert session.flush_count == 0


@async_test
async def test_publish_rejects_missing_observation_count_for_normalized_dataset():
    dataset = make_dataset(data_kind=DatasetKind.OHLCV)
    metadata = make_metadata(record_count=None)

    session = FakeSession([dataset.dataset_id, dataset, make_version(dataset.dataset_id)])
    factory = FakeSessionFactory(session)
    repository = SQLAlchemyDatasetLifecycleRepository(factory)

    with pytest.raises(DatasetLifecycleInvalidPublicationMetadataError):
        await repository.publish_staging_version(
            uuid4(),
            metadata,
        )

    assert factory.created is True
    assert session.flush_count == 0