"""Tests for the SQLAlchemy dataset repository."""

import asyncio
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.repository_errors import (
    DatasetRepositoryDuplicateError,
    DatasetRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.datasets import (
    AssetClass,
    Dataset,
    DatasetKind,
    Timeframe,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_repository import (
    SQLAlchemyDatasetRepository,
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
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return FakeScalars(self._items)


class FakeSession:
    def __init__(
        self,
        get_results=None,
        execute_results=None,
        integrity_error=None,
    ):
        self.get_results = get_results or {}
        self.execute_results = execute_results or []
        self.integrity_error = integrity_error
        self.added = []
        self.flush_count = 0
        self.updated = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    def begin(self):
        return FakeTransaction()

    async def get(self, model, identity):
        return self.get_results.get(model)

    async def execute(self, statement):
        return FakeResult(self.execute_results)

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


def make_dataset(**overrides):
    values = {
        "dataset_id": uuid4(),
        "instrument_id": uuid4(),
        "name": "EURUSD hourly",
        "data_kind": DatasetKind.OHLCV,
        "asset_class": AssetClass.FOREX,
        "instrument_symbol": "EURUSD",
        "provider_id": "dukascopy",
        "timeframe": Timeframe.H1,
    }
    values.update(overrides)
    return Dataset(**values)


def make_orm_dataset(**overrides):
    values = {
        "dataset_id": uuid4(),
        "instrument_id": uuid4(),
        "name": "EURUSD hourly",
        "data_kind": DatasetKind.OHLCV,
        "asset_class": AssetClass.FOREX,
        "instrument_symbol": "EURUSD",
        "provider_id": "dukascopy",
        "timeframe": Timeframe.H1,
        "latest_version_id": None,
    }
    values.update(overrides)
    return DatasetORM(**values)


def test_get_by_id_returns_dataset():
    dataset_id = uuid4()
    orm_dataset = make_orm_dataset(dataset_id=dataset_id)
    session = FakeSession(
        get_results={DatasetORM: orm_dataset}
    )
    repository = SQLAlchemyDatasetRepository(
        FakeSessionFactory(session)
    )

    result = asyncio.run(repository.get_by_id(dataset_id))

    assert result is not None
    assert result.dataset_id == dataset_id
    assert result.name == "EURUSD hourly"
    assert result.instrument_symbol == "EURUSD"


def test_get_by_id_returns_none_when_missing():
    session = FakeSession()
    repository = SQLAlchemyDatasetRepository(
        FakeSessionFactory(session)
    )

    result = asyncio.run(repository.get_by_id(uuid4()))

    assert result is None


def test_list_all_returns_datasets_in_repository_order():
    first = make_orm_dataset()
    second = make_orm_dataset(name="GBPUSD hourly")

    session = FakeSession(execute_results=[first, second])
    repository = SQLAlchemyDatasetRepository(
        FakeSessionFactory(session)
    )

    result = asyncio.run(repository.list_all())

    assert len(result) == 2
    assert result[0].dataset_id == first.dataset_id
    assert result[1].dataset_id == second.dataset_id


def test_add_converts_domain_dataset_to_orm():
    dataset = make_dataset()
    session = FakeSession()
    repository = SQLAlchemyDatasetRepository(
        FakeSessionFactory(session)
    )

    asyncio.run(repository.add(dataset))

    assert len(session.added) == 1
    persisted = session.added[0]
    assert isinstance(persisted, DatasetORM)
    assert persisted.dataset_id == dataset.dataset_id
    assert persisted.name == dataset.name
    assert persisted.data_kind == dataset.data_kind
    assert persisted.asset_class == dataset.asset_class
    assert persisted.instrument_symbol == dataset.instrument_symbol
    assert persisted.provider_id == dataset.provider_id
    assert persisted.timeframe == dataset.timeframe
    assert session.flush_count == 1


def test_add_maps_duplicate_primary_key():
    dataset = make_dataset()

    class FakeDiagnostic:
        constraint_name = "pk_datasets"

    class FakeOriginalError(Exception):
        diag = FakeDiagnostic()

    from sqlalchemy.exc import IntegrityError

    session = FakeSession(
        integrity_error=IntegrityError(
            "INSERT",
            {},
            FakeOriginalError("duplicate"),
        )
    )
    repository = SQLAlchemyDatasetRepository(
        FakeSessionFactory(session)
    )

    with pytest.raises(DatasetRepositoryDuplicateError):
        asyncio.run(repository.add(dataset))


def test_update_changes_name_and_latest_version_only():
    dataset_id = uuid4()
    latest_version_id = uuid4()

    orm_dataset = make_orm_dataset(dataset_id=dataset_id)
    updated = make_dataset(
        dataset_id=dataset_id,
        name="Updated EURUSD hourly",
        latest_version_id=latest_version_id,
    )

    session = FakeSession(
        get_results={DatasetORM: orm_dataset}
    )
    repository = SQLAlchemyDatasetRepository(
        FakeSessionFactory(session)
    )

    asyncio.run(repository.update(updated))

    assert orm_dataset.name == "Updated EURUSD hourly"
    assert orm_dataset.latest_version_id == latest_version_id
    assert orm_dataset.instrument_symbol == "EURUSD"
    assert orm_dataset.provider_id == "dukascopy"
    assert session.flush_count == 1


def test_update_raises_when_dataset_does_not_exist():
    dataset = make_dataset()
    session = FakeSession()
    repository = SQLAlchemyDatasetRepository(
        FakeSessionFactory(session)
    )

    with pytest.raises(DatasetRepositoryRecordNotFoundError):
        asyncio.run(repository.update(dataset))
