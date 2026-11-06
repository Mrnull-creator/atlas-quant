"""Tests for transactional dataset lifecycle persistence."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from atlas_quant.services.market_data.application.dataset_lifecycle_errors import (
    DatasetLifecycleDatasetNotFoundError,
    DatasetLifecycleVersionConflictError,
)
from atlas_quant.services.market_data.domain.datasets import (
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version import (
    DatasetVersionORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_lifecycle_repository import (
    SQLAlchemyDatasetLifecycleRepository,
)


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class FakeResult:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar


class FakeSession:
    def __init__(
        self,
        *,
        dataset=None,
        current_max=0,
        integrity_error=None,
    ):
        self.dataset = dataset
        self.current_max = current_max
        self.integrity_error = integrity_error
        self.statements = []
        self.added = []
        self.flush_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    def begin(self):
        return FakeTransaction()

    async def execute(self, statement):
        self.statements.append(statement)

        if len(self.statements) == 1:
            return FakeResult(self.dataset)

        return FakeResult(self.current_max)

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.flush_count += 1

        if self.integrity_error is not None:
            raise self.integrity_error


class FakeSessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self.session


def make_repository(session):
    return SQLAlchemyDatasetLifecycleRepository(
        FakeSessionFactory(session)
    )


def test_create_next_staging_version_allocates_first_version():
    dataset_id = uuid4()
    session = FakeSession(
        dataset=object(),
        current_max=0,
    )
    repository = make_repository(session)

    version = asyncio.run(
        repository.create_next_staging_version(
            dataset_id,
            schema_version="1",
            storage_format="parquet",
        )
    )

    assert version.dataset_id == dataset_id
    assert version.version_number == 1
    assert version.status == DatasetVersionStatus.STAGING
    assert version.schema_version == "1"
    assert version.storage_format == "parquet"
    assert version.record_count == 0
    assert version.published_at is None

    assert len(session.statements) == 2
    assert len(session.added) == 1
    assert session.flush_count == 1

    persisted = session.added[0]
    assert isinstance(persisted, DatasetVersionORM)
    assert persisted.dataset_id == dataset_id
    assert persisted.version_number == 1


def test_create_next_staging_version_increments_existing_version():
    dataset_id = uuid4()
    session = FakeSession(
        dataset=object(),
        current_max=7,
    )
    repository = make_repository(session)

    version = asyncio.run(
        repository.create_next_staging_version(
            dataset_id,
            schema_version="1",
            storage_format="parquet",
        )
    )

    assert version.version_number == 8
    assert session.added[0].version_number == 8


def test_create_next_staging_version_locks_dataset_row():
    session = FakeSession(
        dataset=object(),
        current_max=0,
    )
    repository = make_repository(session)

    asyncio.run(
        repository.create_next_staging_version(
            uuid4(),
            schema_version="1",
            storage_format="parquet",
        )
    )

    lock_statement = session.statements[0]

    assert lock_statement._for_update_arg is not None


def test_create_next_staging_version_rejects_missing_dataset():
    session = FakeSession(dataset=None)
    repository = make_repository(session)

    with pytest.raises(DatasetLifecycleDatasetNotFoundError):
        asyncio.run(
            repository.create_next_staging_version(
                uuid4(),
                schema_version="1",
                storage_format="parquet",
            )
        )

    assert len(session.statements) == 1
    assert session.added == []
    assert session.flush_count == 0


def test_create_next_staging_version_maps_duplicate_number():
    class FakeDiagnostic:
        constraint_name = (
            "uq_dataset_versions_dataset_version_number"
        )

    class FakeOriginalError(Exception):
        diag = FakeDiagnostic()

    session = FakeSession(
        dataset=object(),
        current_max=2,
        integrity_error=IntegrityError(
            "INSERT",
            {},
            FakeOriginalError("duplicate version"),
        ),
    )
    repository = make_repository(session)

    with pytest.raises(DatasetLifecycleVersionConflictError):
        asyncio.run(
            repository.create_next_staging_version(
                uuid4(),
                schema_version="1",
                storage_format="parquet",
            )
        )