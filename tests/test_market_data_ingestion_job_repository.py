"""Tests for the asynchronous ingestion job repository."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from atlas_quant.services.market_data.application.repository_errors import (
    IngestionJobRepositoryConflictError,
    IngestionJobRepositoryInvalidTransitionError,
    IngestionJobRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJob,
    IngestionJobStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_job import (
    IngestionJobDatasetVersionORM,
    IngestionJobORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_job_repository import (
    SQLAlchemyIngestionJobRepository,
)


def run(coroutine):
    """Execute an asynchronous test operation."""
    return asyncio.run(coroutine)


class FakeTransaction:
    """Minimal asynchronous transaction context manager."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeScalars:
    """Minimal SQLAlchemy scalar result."""

    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class FakeResult:
    """Minimal SQLAlchemy result."""

    def __init__(self, value=None, values=None):
        self._value = value
        self._values = values if values is not None else []

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return FakeScalars(self._values)


class FakeSession:
    """Fake asynchronous session for repository unit tests."""

    def __init__(self, jobs=None, associations=None):
        self.jobs = jobs if jobs is not None else {}
        self.associations = (
            associations if associations is not None else []
        )
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return FakeTransaction()

    async def get(self, model, identity):
        if model is IngestionJobORM:
            return self.jobs.get(identity)

        return None

    def add(self, instance):
        self.added.append(instance)

        if isinstance(instance, IngestionJobORM):
            self.jobs[instance.job_id] = instance

        elif isinstance(instance, IngestionJobDatasetVersionORM):
            self.associations.append(instance)

    async def flush(self):
        return None

    async def execute(self, statement):
        description = statement.column_descriptions[0]
        selected = description["entity"]

        # Handle list_all().
        if (
            selected is IngestionJobORM
            and statement.whereclause is None
        ):
            jobs = sorted(
                self.jobs.values(),
                key=lambda job: (
                    job.created_at,
                    str(job.job_id),
                ),
            )
            return FakeResult(values=jobs)

        # Extract the UUID used by a filtered query.
        parameters = statement.compile().params
        identities = [
            value
            for value in parameters.values()
            if isinstance(value, UUID)
        ]
        identity = identities[0] if identities else None

        # Handle the single-job SELECT used by update().
        if selected is IngestionJobORM:
            return FakeResult(
                value=self.jobs.get(identity)
            )

        # Handle the dataset-version association query used by
        # the repository's ORM-to-domain conversion.
        if description["name"] == "dataset_version_id":
            matching = [
                association.dataset_version_id
                for association in self.associations
                if association.job_id == identity
            ]

            return FakeResult(
                values=sorted(matching, key=str)
            )

        return FakeResult()

    async def close(self):
        return None


class FakeSessionFactory:
    """Factory returning sessions sharing the same fake database."""

    def __init__(self):
        self.jobs = {}
        self.associations = []

    def __call__(self):
        return FakeSession(
            jobs=self.jobs,
            associations=self.associations,
        )


def make_job(**overrides):
    """Build a valid domain ingestion job."""

    values = {
        "job_id": uuid4(),
        "provider_id": "dukascopy",
        "asset_class": AssetClass.FOREX,
        "instrument_id": None,
        "instrument_symbol": "EURUSD",
        "timeframe": Timeframe.M1,
        "requested_start": datetime(
            2025, 1, 1, tzinfo=timezone.utc
        ),
        "requested_end": datetime(
            2025, 1, 2, tzinfo=timezone.utc
        ),
        "created_at": datetime(
            2025, 1, 3, tzinfo=timezone.utc
        ),
    }
    values.update(overrides)

    return IngestionJob(**values)


def make_orm_job(**overrides):
    """Build an ORM job with explicit values for every domain field."""

    values = {
        "job_id": uuid4(),
        "provider_id": "dukascopy",
        "asset_class": AssetClass.FOREX,
        "instrument_id": None,
        "instrument_symbol": "EURUSD",
        "timeframe": Timeframe.M1,
        "requested_start": datetime(
            2025, 1, 1, tzinfo=timezone.utc
        ),
        "requested_end": datetime(
            2025, 1, 2, tzinfo=timezone.utc
        ),
        "status": IngestionJobStatus.PENDING,
        "records_downloaded": 0,
        "records_written": 0,
        "records_rejected": 0,
        "failure_code": None,
        "failure_message": None,
        "created_at": datetime(
            2025, 1, 3, tzinfo=timezone.utc
        ),
        "started_at": None,
        "completed_at": None,
    }
    values.update(overrides)

    return IngestionJobORM(**values)


def make_repository():
    """Create a repository and its backing fake session factory."""

    factory = FakeSessionFactory()
    repository = SQLAlchemyIngestionJobRepository(factory)

    return repository, factory


def test_get_by_id_returns_domain_job():
    repository, factory = make_repository()
    orm_job = make_orm_job()
    factory.jobs[orm_job.job_id] = orm_job

    result = run(repository.get_by_id(orm_job.job_id))

    assert isinstance(result, IngestionJob)
    assert result.job_id == orm_job.job_id
    assert result.provider_id == "dukascopy"
    assert result.instrument_id == orm_job.instrument_id
    assert result.status == IngestionJobStatus.PENDING


def test_get_by_id_restores_instrument_identity():
    repository, factory = make_repository()
    instrument_id = uuid4()
    orm_job = make_orm_job(instrument_id=instrument_id)
    factory.jobs[orm_job.job_id] = orm_job

    result = run(repository.get_by_id(orm_job.job_id))

    assert result.instrument_id == instrument_id


def test_get_by_id_returns_none_for_missing_job():
    repository, _ = make_repository()

    result = run(repository.get_by_id(uuid4()))

    assert result is None


def test_get_by_id_restores_dataset_version_associations():
    repository, factory = make_repository()
    orm_job = make_orm_job()
    factory.jobs[orm_job.job_id] = orm_job

    version_ids = (uuid4(), uuid4())

    factory.associations.extend(
        [
            IngestionJobDatasetVersionORM(
                job_id=orm_job.job_id,
                dataset_version_id=version_id,
            )
            for version_id in version_ids
        ]
    )

    result = run(repository.get_by_id(orm_job.job_id))

    assert result.produced_dataset_version_ids == tuple(
        sorted(version_ids, key=str)
    )


def test_list_all_returns_jobs_in_deterministic_order():
    repository, factory = make_repository()

    later = make_orm_job(
        created_at=datetime(
            2025, 1, 5, tzinfo=timezone.utc
        )
    )
    earlier = make_orm_job(
        created_at=datetime(
            2025, 1, 4, tzinfo=timezone.utc
        )
    )

    factory.jobs[later.job_id] = later
    factory.jobs[earlier.job_id] = earlier

    result = run(repository.list_all())

    assert len(result) == 2
    assert result[0].job_id == earlier.job_id
    assert result[1].job_id == later.job_id


def test_list_all_returns_empty_tuple_when_no_jobs_exist():
    repository, _ = make_repository()

    result = run(repository.list_all())

    assert result == ()


def test_add_persists_job():
    repository, factory = make_repository()
    job = make_job()

    run(repository.add(job))

    assert job.job_id in factory.jobs
    assert factory.jobs[job.job_id].provider_id == job.provider_id
    assert factory.jobs[job.job_id].instrument_id == job.instrument_id


def test_add_persists_dataset_version_associations():
    repository, factory = make_repository()
    version_ids = (uuid4(), uuid4())
    job = make_job(
        produced_dataset_version_ids=version_ids
    )

    run(repository.add(job))

    stored_ids = {
        association.dataset_version_id
        for association in factory.associations
    }

    assert stored_ids == set(version_ids)


def test_add_rejects_duplicate_job_id():
    repository, factory = make_repository()
    job = make_job()

    factory.jobs[job.job_id] = repository._to_orm(job)

    with pytest.raises(IngestionJobRepositoryConflictError):
        run(repository.add(job))


def test_update_rejects_missing_job():
    repository, _ = make_repository()

    with pytest.raises(IngestionJobRepositoryRecordNotFoundError):
        run(repository.update(make_job()))


def test_update_persists_running_status_and_progress():
    repository, factory = make_repository()
    current = make_job()

    factory.jobs[current.job_id] = repository._to_orm(current)

    started_at = datetime(
        2025, 1, 3, 1, tzinfo=timezone.utc
    )

    updated = make_job(
        job_id=current.job_id,
        status=IngestionJobStatus.RUNNING,
        started_at=started_at,
        records_downloaded=20,
    )

    run(repository.update(updated))

    stored = factory.jobs[current.job_id]

    assert stored.status == IngestionJobStatus.RUNNING
    assert stored.started_at == started_at
    assert stored.records_downloaded == 20


def test_update_adds_new_dataset_version_association():
    repository, factory = make_repository()
    current = make_job(
        status=IngestionJobStatus.RUNNING,
        started_at=datetime(
            2025, 1, 3, 1, tzinfo=timezone.utc
        ),
    )

    factory.jobs[current.job_id] = repository._to_orm(current)

    version_id = uuid4()

    updated = make_job(
        job_id=current.job_id,
        status=IngestionJobStatus.RUNNING,
        started_at=current.started_at,
        produced_dataset_version_ids=(version_id,),
    )

    run(repository.update(updated))

    assert [
        association.dataset_version_id
        for association in factory.associations
    ] == [version_id]


def test_update_rejects_immutable_identity_change():
    current = make_job()

    updated = make_job(
        job_id=current.job_id,
        provider_id="another-provider",
    )

    with pytest.raises(
        IngestionJobRepositoryInvalidTransitionError
    ):
        SQLAlchemyIngestionJobRepository._validate_update(
            current,
            updated,
        )


def test_update_rejects_instrument_identity_change():
    instrument_id = uuid4()
    current = make_job(instrument_id=instrument_id)
    updated = make_job(
        job_id=current.job_id,
        instrument_id=uuid4(),
    )

    with pytest.raises(
        IngestionJobRepositoryInvalidTransitionError
    ):
        SQLAlchemyIngestionJobRepository._validate_update(
            current,
            updated,
        )


def test_update_rejects_decreasing_record_counts():
    current = make_job(
        status=IngestionJobStatus.RUNNING,
        started_at=datetime(
            2025, 1, 3, 1, tzinfo=timezone.utc
        ),
        records_downloaded=10,
    )

    updated = make_job(
        job_id=current.job_id,
        status=IngestionJobStatus.RUNNING,
        started_at=current.started_at,
        records_downloaded=5,
    )

    with pytest.raises(
        IngestionJobRepositoryInvalidTransitionError
    ):
        SQLAlchemyIngestionJobRepository._validate_update(
            current,
            updated,
        )


def test_update_rejects_invalid_status_transition():
    current = make_job()

    updated = make_job(
        job_id=current.job_id,
        status=IngestionJobStatus.SUCCEEDED,
        completed_at=datetime(
            2025, 1, 3, 1, tzinfo=timezone.utc
        ),
    )

    with pytest.raises(
        IngestionJobRepositoryInvalidTransitionError
    ):
        SQLAlchemyIngestionJobRepository._validate_update(
            current,
            updated,
        )


def test_update_rejects_removing_produced_versions():
    version_id = uuid4()

    current = make_job(
        status=IngestionJobStatus.RUNNING,
        started_at=datetime(
            2025, 1, 3, 1, tzinfo=timezone.utc
        ),
        produced_dataset_version_ids=(version_id,),
    )

    updated = make_job(
        job_id=current.job_id,
        status=IngestionJobStatus.RUNNING,
        started_at=current.started_at,
    )

    with pytest.raises(
        IngestionJobRepositoryInvalidTransitionError
    ):
        SQLAlchemyIngestionJobRepository._validate_update(
            current,
            updated,
        )


def test_update_rejects_modifying_terminal_job():
    current = make_job(
        status=IngestionJobStatus.FAILED,
        completed_at=datetime(
            2025, 1, 3, 1, tzinfo=timezone.utc
        ),
        failure_message="Provider request failed.",
    )

    with pytest.raises(
        IngestionJobRepositoryInvalidTransitionError
    ):
        SQLAlchemyIngestionJobRepository._validate_update(
            current,
            current,
        )


def test_as_utc_normalizes_naive_database_datetime():
    value = datetime(2025, 1, 1)

    result = SQLAlchemyIngestionJobRepository._as_utc(value)

    assert result == value.replace(tzinfo=timezone.utc)


def test_as_utc_preserves_utc_aware_datetime():
    value = datetime(
        2025, 1, 1, tzinfo=timezone.utc
    )

    result = SQLAlchemyIngestionJobRepository._as_utc(value)

    assert result == value


def test_as_utc_returns_none():
    assert SQLAlchemyIngestionJobRepository._as_utc(None) is None