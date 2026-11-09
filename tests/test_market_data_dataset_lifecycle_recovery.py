"""Tests for dataset lifecycle failure and recovery discovery."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.dataset_lifecycle_errors import (
    DatasetLifecycleDatasetNotFoundError,
    DatasetLifecycleInvalidRecoveryQueryError,
    DatasetLifecycleInvalidVersionStateError,
    DatasetLifecycleVersionNotFoundError,
)
from atlas_quant.services.market_data.domain.datasets import (
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_lifecycle_repository import (
    SQLAlchemyDatasetLifecycleRepository,
)


def run_async(coroutine):
    """Run an async operation without requiring pytest-asyncio."""
    return asyncio.run(coroutine)


class FakeResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.executed_statements = []
        self.flush_count = 0

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

        return self.results.pop(0)

    async def flush(self):
        self.flush_count += 1


class FakeSessionFactory:
    def __init__(self, session):
        self.session = session
        self.call_count = 0

    def __call__(self):
        self.call_count += 1
        return self.session


def make_version(
    *,
    status=DatasetVersionStatus.STAGING,
    created_at=None,
    version_number=1,
):
    now = datetime.now(timezone.utc)

    return SimpleNamespace(
        dataset_version_id=uuid4(),
        dataset_id=uuid4(),
        version_number=version_number,
        schema_version="1",
        storage_format="parquet",
        status=status,
        first_timestamp=None,
        last_timestamp=None,
        record_count=0,
        size_bytes=None,
        storage_uri=None,
        checksum_algorithm=None,
        checksum=None,
        created_at=created_at or now,
        published_at=None,
    )


def make_repository(results):
    session = FakeSession(results)
    factory = FakeSessionFactory(session)

    return (
        SQLAlchemyDatasetLifecycleRepository(factory),
        session,
        factory,
    )


def test_fail_staging_version_changes_status_to_failed():
    dataset_id = uuid4()
    version = make_version()
    version.dataset_id = dataset_id

    latest_version_id = uuid4()
    dataset = SimpleNamespace(
        dataset_id=dataset_id,
        latest_version_id=latest_version_id,
    )

    repository, session, _ = make_repository(
        [
            FakeResult(dataset_id),
            FakeResult(dataset),
            FakeResult(version),
        ]
    )

    result = run_async(
        repository.fail_staging_version(version.dataset_version_id)
    )

    assert result.status == DatasetVersionStatus.FAILED
    assert version.status == DatasetVersionStatus.FAILED
    assert dataset.latest_version_id == latest_version_id
    assert session.flush_count == 1
    assert len(session.executed_statements) == 3


def test_fail_staging_version_does_not_change_latest_pointer():
    dataset_id = uuid4()
    latest_version_id = uuid4()
    version = make_version()
    version.dataset_id = dataset_id

    dataset = SimpleNamespace(
        dataset_id=dataset_id,
        latest_version_id=latest_version_id,
    )

    repository, _, _ = make_repository(
        [
            FakeResult(dataset_id),
            FakeResult(dataset),
            FakeResult(version),
        ]
    )

    run_async(
        repository.fail_staging_version(version.dataset_version_id)
    )

    assert dataset.latest_version_id == latest_version_id


def test_fail_staging_version_rejects_non_staging_version():
    dataset_id = uuid4()
    version = make_version(status=DatasetVersionStatus.FAILED)
    version.dataset_id = dataset_id

    dataset = SimpleNamespace(
        dataset_id=dataset_id,
        latest_version_id=None,
    )

    repository, session, _ = make_repository(
        [
            FakeResult(dataset_id),
            FakeResult(dataset),
            FakeResult(version),
        ]
    )

    with pytest.raises(DatasetLifecycleInvalidVersionStateError):
        run_async(
            repository.fail_staging_version(version.dataset_version_id)
        )

    assert session.flush_count == 0


def test_fail_staging_version_rejects_missing_version():
    repository, session, _ = make_repository(
        [FakeResult(None)]
    )

    with pytest.raises(DatasetLifecycleVersionNotFoundError):
        run_async(repository.fail_staging_version(uuid4()))

    assert len(session.executed_statements) == 1


def test_fail_staging_version_rejects_missing_dataset():
    dataset_id = uuid4()
    version_id = uuid4()

    repository, _, _ = make_repository(
        [
            FakeResult(dataset_id),
            FakeResult(None),
        ]
    )

    with pytest.raises(DatasetLifecycleDatasetNotFoundError):
        run_async(repository.fail_staging_version(version_id))


def test_recovery_query_returns_staging_versions():
    cutoff = datetime.now(timezone.utc)

    first = make_version(
        created_at=cutoff - timedelta(hours=3),
        version_number=1,
    )
    second = make_version(
        created_at=cutoff - timedelta(hours=2),
        version_number=2,
    )

    repository, session, _ = make_repository(
        [FakeResult([first, second])]
    )

    results = run_async(
        repository.list_staging_versions_for_recovery(
            created_before=cutoff,
            limit=10,
        )
    )

    assert len(results) == 2
    assert all(
        item.status == DatasetVersionStatus.STAGING
        for item in results
    )
    assert results[0].dataset_version_id == first.dataset_version_id
    assert results[1].dataset_version_id == second.dataset_version_id
    assert len(session.executed_statements) == 1


def test_recovery_query_returns_empty_list_when_no_candidates_exist():
    repository, _, _ = make_repository(
        [FakeResult([])]
    )

    results = run_async(
        repository.list_staging_versions_for_recovery(
            created_before=datetime.now(timezone.utc),
        )
    )

    assert results == []


def test_recovery_query_rejects_naive_datetime():
    repository, _, factory = make_repository([])

    with pytest.raises(DatasetLifecycleInvalidRecoveryQueryError):
        run_async(
            repository.list_staging_versions_for_recovery(
                created_before=datetime(2026, 1, 1),
            )
        )

    assert factory.call_count == 0


@pytest.mark.parametrize(
    "limit",
    [0, -1, 1001, True, 1.5],
)
def test_recovery_query_rejects_invalid_limit(limit):
    repository, _, factory = make_repository([])

    with pytest.raises(DatasetLifecycleInvalidRecoveryQueryError):
        run_async(
            repository.list_staging_versions_for_recovery(
                created_before=datetime.now(timezone.utc),
                limit=limit,
            )
        )

    assert factory.call_count == 0