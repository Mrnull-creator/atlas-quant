"""Asynchronous SQLAlchemy repository for ingestion jobs."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.market_data.application.repository_errors import (
    IngestionJobRepositoryConflictError,
    IngestionJobRepositoryExecutionOwnershipError,
    IngestionJobRepositoryIntegrityError,
    IngestionJobRepositoryInvalidTransitionError,
    IngestionJobRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.datasets import DatasetKind
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJob,
    IngestionJobStatus,
)
from atlas_quant.services.market_data.domain.ingestion_attempt import (
    IngestionAttempt,
    IngestionAttemptStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_attempt import (
    IngestionAttemptORM,
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

_DEFAULT_LEASE_DURATION = timedelta(seconds=60)


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

    async def claim_execution(self, job_id: UUID) -> bool:
        """Legacy one-time claim API.

        New execution code must use claim_attempt. This method remains
        temporarily to avoid breaking the orchestrator during migration.
        """

        statement = (
            update(IngestionJobORM)
            .where(
                IngestionJobORM.job_id == job_id,
                IngestionJobORM.status == IngestionJobStatus.RUNNING,
                IngestionJobORM.execution_claimed_at.is_(None),
            )
            .values(execution_claimed_at=func.now())
            .returning(IngestionJobORM.job_id)
        )

        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                claimed_job_id = result.scalar_one_or_none()

                return claimed_job_id is not None

    async def claim_attempt(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        lease_duration: timedelta = _DEFAULT_LEASE_DURATION,
    ) -> IngestionAttempt | None:
        """Acquire a new authoritative attempt for a running job.

        The job row is locked first. This serializes competing claims
        for the same job and makes attempt-number/fencing-token
        allocation atomic.
        """

        worker_id = self._validate_worker_id(worker_id)
        self._validate_lease_duration(lease_duration)

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    job_statement = (
                        select(IngestionJobORM)
                        .where(IngestionJobORM.job_id == job_id)
                        .with_for_update()
                    )
                    job_result = await session.execute(job_statement)
                    orm_job = job_result.scalar_one_or_none()

                    if orm_job is None:
                        raise IngestionJobRepositoryRecordNotFoundError(
                            f"Ingestion job {job_id} was not found."
                        )

                    if orm_job.status != IngestionJobStatus.RUNNING:
                        return None

                    # Read the database clock after acquiring the job lock.
                    now_result = await session.execute(
                        select(func.clock_timestamp())
                    )
                    now = self._as_utc(now_result.scalar_one())

                    if orm_job.current_attempt_id is not None:
                        current_statement = (
                            select(IngestionAttemptORM)
                            .where(
                                IngestionAttemptORM.job_id == job_id,
                                IngestionAttemptORM.attempt_id
                                == orm_job.current_attempt_id,
                            )
                            .with_for_update()
                        )
                        current_result = await session.execute(
                            current_statement
                        )
                        current_attempt = (
                            current_result.scalar_one_or_none()
                        )

                        if current_attempt is None:
                            raise IngestionJobRepositoryExecutionOwnershipError(
                                "The job references a current attempt "
                                "that could not be found."
                            )

                        current_expiry = self._as_utc(
                            current_attempt.lease_expires_at
                        )

                        if (
                            current_attempt.status
                            == IngestionAttemptStatus.RUNNING
                            and current_expiry is not None
                            and current_expiry > now
                        ):
                            return None

                        if (
                            current_attempt.status
                            == IngestionAttemptStatus.RUNNING
                        ):
                            current_attempt.status = (
                                IngestionAttemptStatus.LEASE_EXPIRED
                            )
                            current_attempt.finished_at = now
                            await session.flush()

                    counters_statement = select(
                        func.coalesce(
                            func.max(IngestionAttemptORM.attempt_number),
                            0,
                        ),
                        func.coalesce(
                            func.max(IngestionAttemptORM.fencing_token),
                            0,
                        ),
                    ).where(IngestionAttemptORM.job_id == job_id)

                    counters_result = await session.execute(
                        counters_statement
                    )
                    last_attempt_number, last_fencing_token = (
                        counters_result.one()
                    )

                    attempt = IngestionAttempt(
                        attempt_id=uuid4(),
                        job_id=job_id,
                        worker_id=worker_id,
                        attempt_number=last_attempt_number + 1,
                        fencing_token=last_fencing_token + 1,
                        started_at=now,
                        heartbeat_at=now,
                        lease_expires_at=now + lease_duration,
                        status=IngestionAttemptStatus.RUNNING,
                    )

                    orm_attempt = self._attempt_to_orm(attempt)
                    session.add(orm_attempt)

                    # Insert the attempt before assigning the composite
                    # current-attempt foreign key on the job.
                    await session.flush()

                    orm_job.current_attempt_id = attempt.attempt_id
                    await session.flush()

                    return attempt

        except IntegrityError as exc:
            raise IngestionJobRepositoryIntegrityError(
                "The ingestion attempt could not be acquired because "
                "a database constraint was violated."
            ) from exc

    async def renew_attempt_lease(
        self,
        job_id: UUID,
        attempt_id: UUID,
        fencing_token: int,
        worker_id: str,
        *,
        lease_duration: timedelta = _DEFAULT_LEASE_DURATION,
    ) -> IngestionAttempt:
        """Renew a lease only for the current, valid attempt owner."""

        worker_id = self._validate_worker_id(worker_id)
        self._validate_lease_duration(lease_duration)

        async with self._session_factory() as session:
            async with session.begin():
                job_statement = (
                    select(IngestionJobORM)
                    .where(IngestionJobORM.job_id == job_id)
                    .with_for_update()
                )
                job_result = await session.execute(job_statement)
                orm_job = job_result.scalar_one_or_none()

                if orm_job is None:
                    raise IngestionJobRepositoryRecordNotFoundError(
                        f"Ingestion job {job_id} was not found."
                    )

                if orm_job.status != IngestionJobStatus.RUNNING:
                    raise IngestionJobRepositoryExecutionOwnershipError(
                        "The ingestion job is no longer running."
                    )

                if orm_job.current_attempt_id != attempt_id:
                    raise IngestionJobRepositoryExecutionOwnershipError(
                        "The supplied attempt is no longer the job's "
                        "authoritative attempt."
                    )

                attempt_statement = (
                    select(IngestionAttemptORM)
                    .where(
                        IngestionAttemptORM.job_id == job_id,
                        IngestionAttemptORM.attempt_id == attempt_id,
                    )
                    .with_for_update()
                )
                attempt_result = await session.execute(attempt_statement)
                orm_attempt = attempt_result.scalar_one_or_none()

                if orm_attempt is None:
                    raise IngestionJobRepositoryExecutionOwnershipError(
                        "The authoritative ingestion attempt was not found."
                    )

                # Read the database clock only after both locks are held.
                now_result = await session.execute(
                    select(func.clock_timestamp())
                )
                now = self._as_utc(now_result.scalar_one())

                lease_expires_at = self._as_utc(
                    orm_attempt.lease_expires_at
                )

                if (
                    orm_attempt.status != IngestionAttemptStatus.RUNNING
                    or orm_attempt.fencing_token != fencing_token
                    or orm_attempt.worker_id != worker_id
                    or lease_expires_at is None
                    or lease_expires_at <= now
                ):
                    raise IngestionJobRepositoryExecutionOwnershipError(
                        "The attempt is stale, expired, superseded, "
                        "or owned by another worker."
                    )

                orm_attempt.heartbeat_at = now
                orm_attempt.lease_expires_at = now + lease_duration

                await session.flush()

                return self._attempt_to_domain(orm_attempt)

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
        """Update a job that has not entered attempt-based ownership."""

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

                    if orm_job.current_attempt_id is not None:
                        raise IngestionJobRepositoryExecutionOwnershipError(
                            "This ingestion job is attempt-owned. "
                            "Use update_owned() with the current attempt credentials."
                        )

                    current = await self._to_domain(session, orm_job)
                    self._validate_update(current, job)
                    self._apply_job_update(session, orm_job, current, job)
                    await session.flush()

        except IntegrityError as exc:
            raise IngestionJobRepositoryIntegrityError(
                "The ingestion job update or a dataset-version "
                "association violates a database constraint."
            ) from exc

    async def update_owned(
        self,
        job: IngestionJob,
        attempt_id: UUID,
        fencing_token: int,
        worker_id: str,
    ) -> None:
        """Update a job only while the supplied attempt owns its lease.

        The job and attempt are locked in that order. The database clock is
        read after both locks have been acquired so a lease that expires
        while waiting for a lock cannot authorize the update.
        """

        self._validate_worker_id(worker_id)

        if (
            isinstance(fencing_token, bool)
            or not isinstance(fencing_token, int)
            or fencing_token < 1
        ):
            raise ValueError("fencing_token must be a positive integer.")

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    job_statement = (
                        select(IngestionJobORM)
                        .where(IngestionJobORM.job_id == job.job_id)
                        .with_for_update()
                    )
                    job_result = await session.execute(job_statement)
                    orm_job = job_result.scalar_one_or_none()

                    if orm_job is None:
                        raise IngestionJobRepositoryRecordNotFoundError(
                            f"Ingestion job {job.job_id} was not found."
                        )

                    if (
                        orm_job.status != IngestionJobStatus.RUNNING
                        or orm_job.current_attempt_id != attempt_id
                    ):
                        raise IngestionJobRepositoryExecutionOwnershipError(
                            "The supplied attempt is not the current owner "
                            "of this running ingestion job."
                        )

                    attempt_statement = (
                        select(IngestionAttemptORM)
                        .where(
                            IngestionAttemptORM.job_id == job.job_id,
                            IngestionAttemptORM.attempt_id == attempt_id,
                        )
                        .with_for_update()
                    )
                    attempt_result = await session.execute(attempt_statement)
                    orm_attempt = attempt_result.scalar_one_or_none()

                    if orm_attempt is None:
                        raise IngestionJobRepositoryExecutionOwnershipError(
                            "The current ingestion attempt does not exist."
                        )

                    clock_result = await session.execute(
                        select(func.clock_timestamp())
                    )
                    database_now = self._as_utc(clock_result.scalar_one())

                    if (
                        orm_attempt.status != IngestionAttemptStatus.RUNNING
                        or orm_attempt.fencing_token != fencing_token
                        or orm_attempt.worker_id != worker_id
                        or self._as_utc(orm_attempt.lease_expires_at)
                        <= database_now
                    ):
                        raise IngestionJobRepositoryExecutionOwnershipError(
                            "The ingestion attempt is stale, expired, "
                            "or owned by another worker."
                        )

                    current = await self._to_domain(session, orm_job)
                    self._validate_update(current, job)

                    terminal_statuses = {
                        IngestionJobStatus.SUCCEEDED:
                            IngestionAttemptStatus.SUCCEEDED,
                        IngestionJobStatus.PARTIALLY_SUCCEEDED:
                            IngestionAttemptStatus.PARTIALLY_SUCCEEDED,
                        IngestionJobStatus.FAILED:
                            IngestionAttemptStatus.FAILED,
                        IngestionJobStatus.CANCELLED:
                            IngestionAttemptStatus.CANCELLED,
                    }

                    attempt_terminal_status = terminal_statuses.get(job.status)

                    if attempt_terminal_status is not None:
                        orm_attempt.status = attempt_terminal_status
                        orm_attempt.finished_at = database_now

                        if job.status in (
                            IngestionJobStatus.FAILED,
                            IngestionJobStatus.PARTIALLY_SUCCEEDED,
                        ):
                            orm_attempt.failure_code = job.failure_code
                            orm_attempt.failure_message = job.failure_message
                        else:
                            orm_attempt.failure_code = None
                            orm_attempt.failure_message = None

                    self._apply_job_update(session, orm_job, current, job)
                    await session.flush()

        except IntegrityError as exc:
            raise IngestionJobRepositoryIntegrityError(
                "The owned ingestion job update, attempt transition, "
                "or dataset-version association violates a database constraint."
            ) from exc

    @staticmethod
    def _apply_job_update(
        session: AsyncSession,
        orm_job: IngestionJobORM,
        current: IngestionJob,
        job: IngestionJob,
    ) -> None:
        """Apply validated job fields and append new version associations."""

        orm_job.status = job.status
        orm_job.records_downloaded = job.records_downloaded
        orm_job.records_written = job.records_written
        orm_job.records_rejected = job.records_rejected
        orm_job.failure_code = job.failure_code
        orm_job.failure_message = job.failure_message
        orm_job.started_at = job.started_at
        orm_job.completed_at = job.completed_at

        existing_ids = set(current.produced_dataset_version_ids)
        new_ids = set(job.produced_dataset_version_ids) - existing_ids

        for version_id in sorted(new_ids, key=str):
            session.add(
                IngestionJobDatasetVersionORM(
                    job_id=job.job_id,
                    dataset_version_id=version_id,
                )
            )

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
    def _to_orm(job: IngestionJob) -> IngestionJobORM:
        """Convert an ingestion job into its ORM representation."""

        return IngestionJobORM(
            job_id=job.job_id,
            provider_id=job.provider_id,
            asset_class=job.asset_class,
            instrument_id=job.instrument_id,
            instrument_symbol=job.instrument_symbol,
            timeframe=job.timeframe,
            data_kind=(
                job.data_kind.value
                if job.data_kind is not None
                else None
            ),
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
    def _attempt_to_orm(
        attempt: IngestionAttempt,
    ) -> IngestionAttemptORM:
        """Convert an attempt domain object into its ORM representation."""

        return IngestionAttemptORM(
            attempt_id=attempt.attempt_id,
            job_id=attempt.job_id,
            attempt_number=attempt.attempt_number,
            fencing_token=attempt.fencing_token,
            worker_id=attempt.worker_id,
            status=attempt.status,
            started_at=attempt.started_at,
            heartbeat_at=attempt.heartbeat_at,
            lease_expires_at=attempt.lease_expires_at,
            finished_at=attempt.finished_at,
            failure_code=attempt.failure_code,
            failure_message=attempt.failure_message,
        )

    @staticmethod
    def _attempt_to_domain(
        orm_attempt: IngestionAttemptORM,
    ) -> IngestionAttempt:
        """Convert an attempt ORM row into its domain representation."""

        return IngestionAttempt(
            attempt_id=orm_attempt.attempt_id,
            job_id=orm_attempt.job_id,
            worker_id=orm_attempt.worker_id,
            attempt_number=orm_attempt.attempt_number,
            fencing_token=orm_attempt.fencing_token,
            started_at=SQLAlchemyIngestionJobRepository._as_utc(
                orm_attempt.started_at
            ),
            heartbeat_at=SQLAlchemyIngestionJobRepository._as_utc(
                orm_attempt.heartbeat_at
            ),
            lease_expires_at=SQLAlchemyIngestionJobRepository._as_utc(
                orm_attempt.lease_expires_at
            ),
            status=orm_attempt.status,
            finished_at=SQLAlchemyIngestionJobRepository._as_utc(
                orm_attempt.finished_at
            ),
            failure_code=orm_attempt.failure_code,
            failure_message=orm_attempt.failure_message,
        )

    @staticmethod
    def _validate_worker_id(worker_id: str) -> str:
        """Normalize and validate the worker identity."""

        if not isinstance(worker_id, str):
            raise ValueError("worker_id must be a string.")

        normalized = worker_id.strip()

        if not normalized:
            raise ValueError("worker_id cannot be empty.")

        return normalized

    @staticmethod
    def _validate_lease_duration(lease_duration: timedelta) -> None:
        """Require a positive lease duration."""

        if not isinstance(lease_duration, timedelta):
            raise TypeError("lease_duration must be a timedelta.")

        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive.")

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        """Normalize database timestamps to timezone-aware UTC."""

        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)