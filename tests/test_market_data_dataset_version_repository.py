"""Tests for the SQLAlchemy dataset version repository."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.repository_errors import (
    DatasetVersionRepositoryConflictError,
    DatasetVersionRepositoryDatasetNotFoundError,
)
from atlas_quant.services.market_data.domain.datasets import (
    DatasetVersion,
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version import (
    DatasetVersionORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version_repository import (
    SQLAlchemyDatasetVersionRepository,
)


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class FakeResult:
    def __init__(self, item=None, items=None):
        self.item = item
        self.items = items or []

    def scalar_one_or_none(self):
        return self.item

    def scalars(self):
        return FakeScalars(self.items)


class FakeSession:
    def __init__(
        self,
        get_results=None,
        execute_item=None,
        execute_items=None,
        integrity_error=None,
    ):
        self.get_results = get_results or {}
        self.execute_item = execute_item
        self.execute_items = execute_items or []
        self.integrity_error = integrity_error
        self.added = []
        self.flush_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    def begin(self):
        return FakeTransaction()

    async def get(self, model, identity):
        return self.get_results.get(model)

    async def execute(self, statement):
        if self.execute_item is not None:
            return FakeResult(item=self.execute_item)
        return FakeResult(items=self.execute_items)

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


def make_version(**overrides):
    values = {
        "dataset_version_id": uuid4(),
        "dataset_id": uuid4(),
        "version_number": 1,
        "schema_version": "1",
        "storage_format": "parquet",
        "status": DatasetVersionStatus.STAGING,
        "first_timestamp": datetime(
            2025, 1, 1, tzinfo=timezone.utc
        ),
        "last_timestamp": datetime(
            2025, 1, 2, tzinfo=timezone.utc
        ),
        "record_count": 2,
    }
    values.update(overrides)
    return DatasetVersion(**values)


def make_orm_version(**overrides):
    values = {
        "dataset_version_id": uuid4(),
        "dataset_id": uuid4(),
        "version_number": 1,
        "schema_version": "1",
        "storage_format": "parquet",
        "status": DatasetVersionStatus.STAGING,
        "first_timestamp": datetime(
            2025, 1, 1, tzinfo=timezone.utc
        ),
        "last_timestamp": datetime(
            2025, 1, 2, tzinfo=timezone.utc
        ),
        "record_count": 2,
        "storage_uri": None,
        "checksum_algorithm": None,
        "checksum": None,
        "created_at": datetime(
            2025, 1, 3, tzinfo=timezone.utc
        ),
        "published_at": None,
    }
    values.update(overrides)
    return DatasetVersionORM(**values)


def test_get_by_id_returns_version():
    version_id = uuid4()
    orm_version = make_orm_version(
        dataset_version_id=version_id
    )

    session = FakeSession(
        get_results={DatasetVersionORM: orm_version}
    )
    repository = SQLAlchemyDatasetVersionRepository(
        FakeSessionFactory(session)
    )

    result = asyncio.run(repository.get_by_id(version_id))

    assert result is not None
    assert result.dataset_version_id == version_id
    assert result.version_number == 1
    assert result.schema_version == "1"


def test_get_by_id_returns_none_when_missing():
    session = FakeSession()
    repository = SQLAlchemyDatasetVersionRepository(
        FakeSessionFactory(session)
    )

    result = asyncio.run(repository.get_by_id(uuid4()))

    assert result is None


def test_get_by_version_number_returns_version():
    dataset_id = uuid4()
    orm_version = make_orm_version(dataset_id=dataset_id)

    session = FakeSession(execute_item=orm_version)
    repository = SQLAlchemyDatasetVersionRepository(
        FakeSessionFactory(session)
    )

    result = asyncio.run(
        repository.get_by_version_number(dataset_id, 1)
    )

    assert result is not None
    assert result.dataset_id == dataset_id
    assert result.version_number == 1


def test_get_by_version_number_returns_none_when_missing():
    session = FakeSession()
    repository = SQLAlchemyDatasetVersionRepository(
        FakeSessionFactory(session)
    )

    result = asyncio.run(
        repository.get_by_version_number(uuid4(), 1)
    )

    assert result is None


def test_list_by_dataset_id_returns_versions():
    dataset_id = uuid4()
    first = make_orm_version(
        dataset_id=dataset_id,
        version_number=1,
    )
    second = make_orm_version(
        dataset_id=dataset_id,
        version_number=2,
    )

    session = FakeSession(execute_items=[first, second])
    repository = SQLAlchemyDatasetVersionRepository(
        FakeSessionFactory(session)
    )

    result = asyncio.run(
        repository.list_by_dataset_id(dataset_id)
    )

    assert len(result) == 2
    assert result[0].version_number == 1
    assert result[1].version_number == 2
    assert all(item.dataset_id == dataset_id for item in result)


def test_add_converts_domain_version_to_orm():
    version = make_version()
    parent_dataset = object()

    session = FakeSession(
        get_results={DatasetORM: parent_dataset}
    )
    repository = SQLAlchemyDatasetVersionRepository(
        FakeSessionFactory(session)
    )

    asyncio.run(repository.add(version))

    assert len(session.added) == 1
    persisted = session.added[0]
    assert isinstance(persisted, DatasetVersionORM)
    assert persisted.dataset_version_id == version.dataset_version_id
    assert persisted.dataset_id == version.dataset_id
    assert persisted.version_number == version.version_number
    assert persisted.schema_version == version.schema_version
    assert persisted.record_count == version.record_count
    assert session.flush_count == 1


def test_add_raises_when_parent_dataset_does_not_exist():
    version = make_version()
    session = FakeSession()
    repository = SQLAlchemyDatasetVersionRepository(
        FakeSessionFactory(session)
    )

    with pytest.raises(DatasetVersionRepositoryDatasetNotFoundError):
        asyncio.run(repository.add(version))

    assert session.added == []
    assert session.flush_count == 0


def test_add_maps_duplicate_version_number():
    version = make_version()

    class FakeDiagnostic:
        constraint_name = (
            "uq_dataset_versions_dataset_version_number"
        )

    class FakeOriginalError(Exception):
        diag = FakeDiagnostic()

    from sqlalchemy.exc import IntegrityError

    session = FakeSession(
        get_results={DatasetORM: object()},
        integrity_error=IntegrityError(
            "INSERT",
            {},
            FakeOriginalError("duplicate"),
        ),
    )
    repository = SQLAlchemyDatasetVersionRepository(
        FakeSessionFactory(session)
    )

    with pytest.raises(DatasetVersionRepositoryConflictError):
        asyncio.run(repository.add(version))


def test_as_utc_assigns_utc_to_naive_datetime():
    value = datetime(2025, 1, 1, 12, 0)

    result = SQLAlchemyDatasetVersionRepository._as_utc(value)

    assert result == value.replace(tzinfo=timezone.utc)


def test_as_utc_converts_aware_datetime():
    from datetime import timedelta

    value = datetime(
        2025, 1, 1, 12, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    result = SQLAlchemyDatasetVersionRepository._as_utc(value)

    assert result == datetime(
        2025, 1, 1, 9, 0,
        tzinfo=timezone.utc,
    )


def test_as_utc_preserves_none():
    assert SQLAlchemyDatasetVersionRepository._as_utc(None) is None