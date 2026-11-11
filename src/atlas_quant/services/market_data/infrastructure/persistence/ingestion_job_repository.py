"""Asynchronous SQLAlchemy repository for ingestion jobs."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.market_data.application.repository_errors import (
    IngestionJobRepositoryConflictError,
    IngestionJobRepositoryIntegrityError,
    IngestionJobRepositoryInvalidTransitionError,
    IngestionJobRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.datasets import DatasetKind
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJob,
    IngestionJobStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_job import (
    IngestionJobDatasetVersionORM,
    IngestionJobORM,
)


_ALLOWED_TRANSITIONS = {
    IngestionJobStatus.PENDING: {
        IngestionJobStatus.PENDING,
        IngestionJobStatus.RUNNING,
        IngestionJobStatus.CANCELLED,
    },
    IngestionJobStatus.RUNNING: {
        IngestionJobStatus.RUNNING,
        IngestionJobStatus.SUCCEEDED,
        IngestionJobStatus.PARTIALLY_SUCCEEDED,
        IngestionJobStatus.FAILED,
        IngestionJobStatus.CANCELLED,
    },
}


class SQLAlchemyIngestionJobRepository:
    """Persist ingestion jobs and enforce their lifecycle."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get_by_id(
        self,
        job_id: UUID,
    ) -> IngestionJob | None:
        """Retrieve an ingestion job by its permanent UUID."""

        async with self._session_factory() as session:
            orm_job = await session.get(IngestionJobORM, job_id)

            if orm_job is None:
                return None

            return await self._to_domain(session, orm_job)

    async def list_all(self) -> tuple[IngestionJob, ...]:
        """Retrieve all ingestion jobs in deterministic order."""

        statement = select(IngestionJobORM).order_by(
            IngestionJobORM.created_at,
            IngestionJobORM.job_id,
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            orm_jobs = result.scalars().all()

            jobs = []
            for orm_job in orm_jobs:
                jobs.append(await self._to_domain(session, orm_job))

            return tuple(jobs)

    async def add(self, job: IngestionJob) -> None:
        """Persist a new ingestion job and its output associations."""

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    if await session.get(IngestionJobORM, job.job_id):
                        raise IngestionJobRepositoryConflictError(
                            f"Ingestion job {job.job_id} already exists."
                        )

                    session.add(self._to_orm(job))
                    await session.flush()

                    for version_id in sorted(
                        job.produced_dataset_version_ids,
                        key=str,
                    ):
                        session.add(
                            IngestionJobDatasetVersionORM(
                                job_id=job.job_id,
                                dataset_version_id=version_id,
                            )
                        )

                    await session.flush()

        except IntegrityError as exc:
            raise IngestionJobRepositoryIntegrityError(
                "The ingestion job or one of its dataset-version "
                "associations violates a database constraint."
            ) from exc

    async def update(self, job: IngestionJob) -> None:
        """Update a job while enforcing immutable identity and lifecycle."""

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    statement = (
                        select(IngestionJobORM)
                        .where(IngestionJobORM.job_id == job.job_id)
                        .with_for_update()
                    )
                    result = await session.execute(statement)
                    orm_job = result.scalar_one_or_none()

                    if orm_job is None:
                        raise IngestionJobRepositoryRecordNotFoundError(
                            f"Ingestion job {job.job_id} was not found."
                        )

                    current = await self._to_domain(session, orm_job)
                    self._validate_update(current, job)

                    orm_job.status = job.status
                    orm_job.records_downloaded = job.records_downloaded
                    orm_job.records_written = job.records_written
                    orm_job.records_rejected = job.records_rejected
                    orm_job.failure_code = job.failure_code
                    orm_job.failure_message = job.failure_message
                    orm_job.started_at = job.started_at
                    orm_job.completed_at = job.completed_at

                    existing_ids = set(
                        current.produced_dataset_version_ids
                    )
                    new_ids = (
                        set(job.produced_dataset_version_ids)
                        - existing_ids
                    )

                    for version_id in sorted(new_ids, key=str):
                        session.add(
                            IngestionJobDatasetVersionORM(
                                job_id=job.job_id,
                                dataset_version_id=version_id,
                            )
                        )

                    await session.flush()

        except IntegrityError as exc:
            raise IngestionJobRepositoryIntegrityError(
                "The ingestion job update or a dataset-version "
                "association violates a database constraint."
            ) from exc

    @staticmethod
    def _validate_update(
        current: IngestionJob,
        updated: IngestionJob,
    ) -> None:
        """Validate immutable identity, monotonic progress, and transitions."""

        immutable_fields = (
            "job_id",
            "provider_id",
            "asset_class",
            "instrument_id",
            "instrument_symbol",
            "timeframe",
            "data_kind",
            "provider_mapping_id",
            "requested_start",
            "requested_end",
            "created_at",
        )

        for field_name in immutable_fields:
            if getattr(current, field_name) != getattr(updated, field_name):
                raise IngestionJobRepositoryInvalidTransitionError(
                    f"Ingestion job field '{field_name}' is immutable."
                )

        allowed = _ALLOWED_TRANSITIONS.get(current.status, set())

        if updated.status not in allowed:
            raise IngestionJobRepositoryInvalidTransitionError(
                f"Transition from '{current.status.value}' to "
                f"'{updated.status.value}' is not permitted."
            )

        if current.status not in (
            IngestionJobStatus.PENDING,
            IngestionJobStatus.RUNNING,
        ):
            if current != updated:
                raise IngestionJobRepositoryInvalidTransitionError(
                    "Terminal ingestion jobs are immutable."
                )
            return

        for field_name in (
            "records_downloaded",
            "records_written",
            "records_rejected",
        ):
            if getattr(updated, field_name) < getattr(current, field_name):
                raise IngestionJobRepositoryInvalidTransitionError(
                    f"'{field_name}' cannot decrease."
                )

        if current.started_at is not None:
            if updated.started_at != current.started_at:
                raise IngestionJobRepositoryInvalidTransitionError(
                    "started_at cannot change after a job starts."
                )

        if current.completed_at is not None:
            if updated.completed_at != current.completed_at:
                raise IngestionJobRepositoryInvalidTransitionError(
                    "completed_at cannot change once assigned."
                )

        if not set(current.produced_dataset_version_ids).issubset(
            updated.produced_dataset_version_ids
        ):
            raise IngestionJobRepositoryInvalidTransitionError(
                "Produced dataset-version associations cannot be removed."
            )

    @staticmethod
    async def _to_domain(
        session: AsyncSession,
        orm_job: IngestionJobORM,
    ) -> IngestionJob:
        """Convert a persistence model into the domain representation."""

        statement = (
            select(IngestionJobDatasetVersionORM.dataset_version_id)
            .where(
                IngestionJobDatasetVersionORM.job_id == orm_job.job_id
            )
            .order_by(
                IngestionJobDatasetVersionORM.dataset_version_id
            )
        )
        result = await session.execute(statement)
        version_ids = tuple(result.scalars().all())

        return IngestionJob(
            job_id=orm_job.job_id,
            provider_id=orm_job.provider_id,
            asset_class=orm_job.asset_class,
            instrument_id=orm_job.instrument_id,
            instrument_symbol=orm_job.instrument_symbol,
            timeframe=orm_job.timeframe,
            data_kind=(
                DatasetKind(orm_job.data_kind)
                if orm_job.data_kind is not None
                else None
            ),
            provider_mapping_id=orm_job.provider_mapping_id,
            requested_start=SQLAlchemyIngestionJobRepository._as_utc(
                orm_job.requested_start
            ),
            requested_end=SQLAlchemyIngestionJobRepository._as_utc(
                orm_job.requested_end
            ),
            status=orm_job.status,
            records_downloaded=orm_job.records_downloaded,
            records_written=orm_job.records_written,
            records_rejected=orm_job.records_rejected,
            produced_dataset_version_ids=version_ids,
            failure_code=orm_job.failure_code,
            failure_message=orm_job.failure_message,
            created_at=SQLAlchemyIngestionJobRepository._as_utc(
                orm_job.created_at
            ),
            started_at=SQLAlchemyIngestionJobRepository._as_utc(
                orm_job.started_at
            ),
            completed_at=SQLAlchemyIngestionJobRepository._as_utc(
                orm_job.completed_at
            ),
        )

    @staticmethod
    def _to_orm(job: IngestionJob) -> IngestionJobORM:
        """Convert a domain job into its persistence representation."""

        return IngestionJobORM(
            job_id=job.job_id,
            provider_id=job.provider_id,
            asset_class=job.asset_class,
            instrument_id=job.instrument_id,
            instrument_symbol=job.instrument_symbol,
            timeframe=job.timeframe,
            data_kind=job.data_kind,
            provider_mapping_id=job.provider_mapping_id,
            requested_start=job.requested_start,
            requested_end=job.requested_end,
            status=job.status,
            records_downloaded=job.records_downloaded,
            records_written=job.records_written,
            records_rejected=job.records_rejected,
            failure_code=job.failure_code,
            failure_message=job.failure_message,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        """Normalize database timestamps to timezone-aware UTC."""

        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)