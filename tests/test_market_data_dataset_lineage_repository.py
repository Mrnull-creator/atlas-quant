"""Tests for the SQLAlchemy dataset-version lineage repository."""

import asyncio
from dataclasses import FrozenInstanceError
from uuid import UUID, uuid4

import pytest

from atlas_quant.services.market_data.application.repository_errors import (
    DatasetVersionLineageRepositoryConflictError,
)
from atlas_quant.services.market_data.domain.dataset_lineage import (
    DatasetLineageRelationship,
    DatasetLineageSourceType,
    DatasetVersionLineage,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_lineage import (
    DatasetVersionLineageORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_lineage_repository import (
    SQLAlchemyDatasetVersionLineageRepository,
)


def run(coroutine):
    """Execute an asynchronous test operation."""

    return asyncio.run(coroutine)


class FakeTransaction:
    """Minimal asynchronous transaction context."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeScalars:
    """Minimal SQLAlchemy scalar result."""

    def __init__(self, records):
        self._records = records

    def all(self):
        return list(self._records)


class FakeSession:
    """In-memory asynchronous session for repository tests."""

    def __init__(self):
        self.records = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return FakeTransaction()

    async def get(self, model, identity):
        return self.records.get(identity)

    def add(self, record):
        self.records[record.lineage_id] = record

    async def flush(self):
        return None

    async def scalars(self, statement):
        parameters = statement.compile().params
        records = list(self.records.values())

        for key, value in parameters.items():
            if "target_dataset_version_id" in key:
                records = [
                    record
                    for record in records
                    if record.target_dataset_version_id == value
                ]

            elif "source_type" in key:
                records = [
                    record
                    for record in records
                    if record.source_type == value
                ]

            elif "ingestion_job_id" in key:
                records = [
                    record
                    for record in records
                    if record.ingestion_job_id == value
                ]

            elif "source_dataset_version_id" in key:
                records = [
                    record
                    for record in records
                    if record.source_dataset_version_id == value
                ]

        if any(
            "target_dataset_version_id" in key
            for key in parameters
        ) and not any(
            "source_type" in key
            for key in parameters
        ):
            records.sort(key=lambda item: str(item.lineage_id))
        else:
            records.sort(
                key=lambda item: (
                    str(item.target_dataset_version_id),
                    str(item.lineage_id),
                )
            )

        return FakeScalars(records)


class FakeSessionFactory:
    """Return a shared in-memory session."""

    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self.session


def make_lineage(
    *,
    lineage_id=None,
    target_id=None,
    source_type=DatasetLineageSourceType.INGESTION_JOB,
    source_id=None,
):
    """Create a valid lineage domain object."""

    if source_type == DatasetLineageSourceType.INGESTION_JOB:
        relationship = DatasetLineageRelationship.INGESTED_FROM
    else:
        relationship = DatasetLineageRelationship.DERIVED_FROM

    return DatasetVersionLineage(
        lineage_id=lineage_id or uuid4(),
        target_dataset_version_id=target_id or uuid4(),
        source_type=source_type,
        source_id=source_id or uuid4(),
        relationship=relationship,
    )


def make_repository():
    """Create a repository with an in-memory session."""

    session = FakeSession()
    repository = SQLAlchemyDatasetVersionLineageRepository(
        FakeSessionFactory(session)
    )
    return repository, session


def to_orm(lineage):
    """Convert a domain object to its ORM representation."""

    return SQLAlchemyDatasetVersionLineageRepository._to_orm(lineage)


def test_get_by_id_returns_ingestion_job_lineage():
    repository, session = make_repository()
    lineage = make_lineage()
    session.add(to_orm(lineage))

    result = run(repository.get_by_id(lineage.lineage_id))

    assert result == lineage


def test_get_by_id_returns_dataset_version_lineage():
    repository, session = make_repository()
    lineage = make_lineage(
        source_type=DatasetLineageSourceType.DATASET_VERSION
    )
    session.add(to_orm(lineage))

    result = run(repository.get_by_id(lineage.lineage_id))

    assert result == lineage


def test_get_by_id_returns_none_for_missing_lineage():
    repository, _ = make_repository()

    result = run(repository.get_by_id(uuid4()))

    assert result is None


def test_list_by_target_version_id_returns_matching_records():
    repository, session = make_repository()
    target_id = uuid4()

    first = make_lineage(target_id=target_id)
    second = make_lineage(
        target_id=target_id,
        source_type=DatasetLineageSourceType.DATASET_VERSION,
    )
    unrelated = make_lineage()

    for lineage in (second, unrelated, first):
        session.add(to_orm(lineage))

    result = run(
        repository.list_by_target_version_id(target_id)
    )

    assert len(result) == 2
    assert {item.lineage_id for item in result} == {
        first.lineage_id,
        second.lineage_id,
    }


def test_list_by_target_version_id_returns_empty_tuple():
    repository, _ = make_repository()

    result = run(
        repository.list_by_target_version_id(uuid4())
    )

    assert result == ()


def test_list_by_source_finds_ingestion_job_relationships():
    repository, session = make_repository()
    job_id = uuid4()

    first = make_lineage(source_id=job_id)
    second = make_lineage(source_id=job_id)
    unrelated = make_lineage()

    for lineage in (unrelated, second, first):
        session.add(to_orm(lineage))

    result = run(
        repository.list_by_source(
            DatasetLineageSourceType.INGESTION_JOB,
            job_id,
        )
    )

    assert len(result) == 2
    assert {item.lineage_id for item in result} == {
        first.lineage_id,
        second.lineage_id,
    }


def test_list_by_source_finds_dataset_version_relationships():
    repository, session = make_repository()
    source_id = uuid4()

    first = make_lineage(
        source_id=source_id,
        source_type=DatasetLineageSourceType.DATASET_VERSION,
    )
    second = make_lineage(
        source_id=source_id,
        source_type=DatasetLineageSourceType.DATASET_VERSION,
    )
    unrelated = make_lineage(
        source_type=DatasetLineageSourceType.DATASET_VERSION
    )

    for lineage in (unrelated, second, first):
        session.add(to_orm(lineage))

    result = run(
        repository.list_by_source(
            DatasetLineageSourceType.DATASET_VERSION,
            source_id,
        )
    )

    assert len(result) == 2
    assert {item.lineage_id for item in result} == {
        first.lineage_id,
        second.lineage_id,
    }


def test_list_by_source_returns_empty_tuple():
    repository, _ = make_repository()

    result = run(
        repository.list_by_source(
            DatasetLineageSourceType.INGESTION_JOB,
            uuid4(),
        )
    )

    assert result == ()


def test_add_persists_ingestion_job_lineage():
    repository, session = make_repository()
    lineage = make_lineage()

    run(repository.add(lineage))

    stored = session.records[lineage.lineage_id]

    assert isinstance(stored, DatasetVersionLineageORM)
    assert stored.target_dataset_version_id == (
        lineage.target_dataset_version_id
    )
    assert stored.source_type == DatasetLineageSourceType.INGESTION_JOB
    assert stored.ingestion_job_id == lineage.source_id
    assert stored.source_dataset_version_id is None
    assert stored.relationship_type == (
        DatasetLineageRelationship.INGESTED_FROM
    )


def test_add_persists_dataset_version_lineage():
    repository, session = make_repository()
    lineage = make_lineage(
        source_type=DatasetLineageSourceType.DATASET_VERSION
    )

    run(repository.add(lineage))

    stored = session.records[lineage.lineage_id]

    assert stored.ingestion_job_id is None
    assert stored.source_dataset_version_id == lineage.source_id
    assert stored.relationship_type == (
        DatasetLineageRelationship.DERIVED_FROM
    )


def test_add_rejects_duplicate_lineage_id():
    repository, session = make_repository()
    lineage = make_lineage()
    session.add(to_orm(lineage))

    with pytest.raises(
        DatasetVersionLineageRepositoryConflictError
    ):
        run(repository.add(lineage))


def test_domain_rejects_self_referencing_lineage():
    version_id = uuid4()

    with pytest.raises(ValueError, match="cannot be its own"):
        DatasetVersionLineage(
            target_dataset_version_id=version_id,
            source_type=DatasetLineageSourceType.DATASET_VERSION,
            source_id=version_id,
            relationship=DatasetLineageRelationship.DERIVED_FROM,
        )


def test_domain_rejects_invalid_ingestion_relationship():
    with pytest.raises(ValueError, match="INGESTED_FROM"):
        DatasetVersionLineage(
            target_dataset_version_id=uuid4(),
            source_type=DatasetLineageSourceType.INGESTION_JOB,
            source_id=uuid4(),
            relationship=DatasetLineageRelationship.DERIVED_FROM,
        )


def test_domain_rejects_invalid_dataset_version_relationship():
    with pytest.raises(ValueError, match="DERIVED_FROM"):
        DatasetVersionLineage(
            target_dataset_version_id=uuid4(),
            source_type=DatasetLineageSourceType.DATASET_VERSION,
            source_id=uuid4(),
            relationship=DatasetLineageRelationship.INGESTED_FROM,
        )


def test_lineage_domain_object_is_immutable():
    lineage = make_lineage()

    with pytest.raises(FrozenInstanceError):
        lineage.source_id = uuid4()


def test_orm_conversion_preserves_lineage_identity():
    lineage = make_lineage(
        source_type=DatasetLineageSourceType.DATASET_VERSION
    )

    restored = (
        SQLAlchemyDatasetVersionLineageRepository._to_domain(
            to_orm(lineage)
        )
    )

    assert restored == lineage


def test_lineage_uses_uuid_identifiers():
    lineage = make_lineage()

    assert isinstance(lineage.lineage_id, UUID)
    assert isinstance(lineage.target_dataset_version_id, UUID)
    assert isinstance(lineage.source_id, UUID)