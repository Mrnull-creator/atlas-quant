"""Transactional SQLAlchemy repository for dataset lifecycle operations."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.market_data.application.repository_errors import (
    IngestionJobRepositoryExecutionOwnershipError,
)
from atlas_quant.services.market_data.application.dataset_lifecycle_errors import (
    DatasetLifecycleDatasetNotFoundError,
    DatasetLifecycleInvalidPublicationMetadataError,
    DatasetLifecycleInvalidRecoveryQueryError,
    DatasetLifecycleInvalidVersionStateError,
    DatasetLifecycleVersionConflictError,
    DatasetLifecycleVersionNotFoundError,
)
from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.domain.datasets import (
    DatasetKind,
    DatasetVersion,
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJobStatus,
)
from atlas_quant.services.market_data.domain.ingestion_attempt import (
    IngestionAttempt,
    IngestionAttemptStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version import (
    DatasetVersionORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_attempt import (
    IngestionAttemptORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_job import (
    IngestionJobORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider_instrument_mapping import (
    ProviderInstrumentMappingORM,
)


class SQLAlchemyDatasetLifecycleRepository:
    """Coordinate dataset lifecycle changes within database transactions."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def create_next_staging_version(
        self,
        dataset_id: UUID,
        *,
        schema_version: str,
        storage_format: str,
        attempt: IngestionAttempt | None = None,
    ) -> DatasetVersion:
        """Allocate a staging version, optionally fenced by an attempt.

        Attempt-owned creation locks job, attempt, then dataset. Standalone
        publication workflows may omit the attempt context.
        """

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    if attempt is not None:
                        job_result = await session.execute(
                            select(IngestionJobORM)
                            .where(
                                IngestionJobORM.job_id == attempt.job_id
                            )
                            .with_for_update()
                        )
                        job = job_result.scalar_one_or_none()

                        if (
                            job is None
                            or job.status != IngestionJobStatus.RUNNING
                            or job.current_attempt_id != attempt.attempt_id
                        ):
                            raise IngestionJobRepositoryExecutionOwnershipError(
                                "The ingestion attempt is no longer the "
                                "current owner of its job."
                            )

                        attempt_result = await session.execute(
                            select(IngestionAttemptORM)
                            .where(
                                IngestionAttemptORM.attempt_id
                                == attempt.attempt_id,
                                IngestionAttemptORM.job_id == attempt.job_id,
                            )
                            .with_for_update()
                        )
                        persisted_attempt = attempt_result.scalar_one_or_none()

                        database_now_result = await session.execute(
                            select(func.clock_timestamp())
                        )
                        database_now = database_now_result.scalar_one()

                        if (
                            persisted_attempt is None
                            or persisted_attempt.status
                            != IngestionAttemptStatus.RUNNING
                            or persisted_attempt.worker_id != attempt.worker_id
                            or persisted_attempt.fencing_token
                            != attempt.fencing_token
                            or persisted_attempt.lease_expires_at
                            <= database_now
                        ):
                            raise IngestionJobRepositoryExecutionOwnershipError(
                                "The ingestion attempt has expired or its "
                                "ownership credentials are stale."
                            )

                        if (
                            job.provider_mapping_id is None
                            or job.instrument_id is None
                            or job.data_kind is None
                        ):
                            raise IngestionJobRepositoryExecutionOwnershipError(
                                "The ingestion job does not contain a "
                                "complete normalized dataset identity."
                            )

                        mapping_result = await session.execute(
                            select(ProviderInstrumentMappingORM).where(
                                ProviderInstrumentMappingORM.mapping_id
                                == job.provider_mapping_id
                            )
                        )
                        mapping = mapping_result.scalar_one_or_none()

                        if (
                            mapping is None
                            or not mapping.is_active
                            or mapping.provider_id != job.provider_id
                            or mapping.instrument_id != job.instrument_id
                        ):
                            raise IngestionJobRepositoryExecutionOwnershipError(
                                "The ingestion job's provider mapping does "
                                "not match its provider and instrument."
                            )

                    lock_statement = (
                        select(DatasetORM)
                        .where(DatasetORM.dataset_id == dataset_id)
                        .with_for_update()
                    )

                    result = await session.execute(lock_statement)
                    dataset = result.scalar_one_or_none()

                    if dataset is None:
                        raise DatasetLifecycleDatasetNotFoundError(
                            f"Dataset {dataset_id} was not found."
                        )

                    if attempt is not None:
                        if (
                            dataset.data_kind != job.data_kind
                            or dataset.asset_class != job.asset_class
                            or dataset.instrument_id != job.instrument_id
                            or (
                                dataset.data_kind == DatasetKind.OHLCV
                                and dataset.timeframe != job.timeframe
                            )
                        ):
                            raise IngestionJobRepositoryExecutionOwnershipError(
                                "The target dataset identity does not match "
                                "the ingestion job."
                            )

                    max_version_statement = select(
                        func.coalesce(
                            func.max(DatasetVersionORM.version_number),
                            0,
                        )
                    ).where(
                        DatasetVersionORM.dataset_id == dataset_id
                    )

                    result = await session.execute(max_version_statement)
                    current_max = result.scalar_one()

                    version = DatasetVersion(
                        dataset_id=dataset_id,
                        version_number=current_max + 1,
                        schema_version=schema_version,
                        storage_format=storage_format,
                        status=DatasetVersionStatus.STAGING,
                        ingestion_attempt_id=(
                            attempt.attempt_id if attempt is not None else None
                        ),
                    )

                    session.add(self._to_orm(version))
                    await session.flush()

                    return version

        except IntegrityError as exc:
            if self._is_version_conflict(exc):
                raise DatasetLifecycleVersionConflictError(
                    "The next dataset version conflicts with an existing "
                    "version identity or number."
                ) from exc

            raise

    async def _validate_publication_attempt_ownership(
        self,
        session,
        *,
        attempt: IngestionAttempt | None,
        version_attempt_id: UUID | None,
    ) -> None:
        """Validate attempt ownership before locking dataset publication rows.

        The lock order is job -> attempt -> dataset -> version.
        Standalone versions remain valid only when no attempt is supplied.
        """

        if version_attempt_id is None:
            if attempt is not None:
                raise IngestionJobRepositoryExecutionOwnershipError(
                    "An ingestion attempt cannot publish a standalone "
                    "dataset version."
                )
            return

        if attempt is None or attempt.attempt_id != version_attempt_id:
            raise IngestionJobRepositoryExecutionOwnershipError(
                "The staging version requires its originating ingestion "
                "attempt."
            )

        job_result = await session.execute(
            select(IngestionJobORM)
            .where(IngestionJobORM.job_id == attempt.job_id)
            .with_for_update()
        )
        job = job_result.scalar_one_or_none()

        if (
            job is None
            or job.status != IngestionJobStatus.RUNNING
            or job.current_attempt_id != attempt.attempt_id
        ):
            raise IngestionJobRepositoryExecutionOwnershipError(
                "The ingestion attempt is no longer the current owner "
                "of its job."
            )

        attempt_result = await session.execute(
            select(IngestionAttemptORM)
            .where(
                IngestionAttemptORM.attempt_id == attempt.attempt_id,
                IngestionAttemptORM.job_id == attempt.job_id,
            )
            .with_for_update()
        )
        persisted_attempt = attempt_result.scalar_one_or_none()

        database_now_result = await session.execute(
            select(func.clock_timestamp())
        )
        database_now = database_now_result.scalar_one()

        if (
            persisted_attempt is None
            or persisted_attempt.status != IngestionAttemptStatus.RUNNING
            or persisted_attempt.worker_id != attempt.worker_id
            or persisted_attempt.fencing_token != attempt.fencing_token
            or persisted_attempt.lease_expires_at <= database_now
        ):
            raise IngestionJobRepositoryExecutionOwnershipError(
                "The ingestion attempt has expired or its ownership "
                "credentials are stale."
            )

    async def publish_staging_version(
        self,
        dataset_version_id: UUID,
        metadata: StorageObjectMetadata,
        attempt: IngestionAttempt | None = None,
    ) -> DatasetVersion:
        """Publish a verified version and advance the latest pointer atomically.

        Attempt-bound versions require the active originating attempt.
        Standalone versions retain the existing publication behavior.

        The transaction locks job, attempt, dataset, then version.
        """

        if metadata.record_count == 0:
            raise DatasetLifecycleInvalidPublicationMetadataError(
                "Published dataset versions cannot contain zero records."
            )

        async with self._session_factory() as session:
            async with session.begin():
                identity_result = await session.execute(
                    select(
                        DatasetVersionORM.dataset_id,
                        DatasetVersionORM.ingestion_attempt_id,
                    ).where(
                        DatasetVersionORM.dataset_version_id
                        == dataset_version_id
                    )
                )
                identity = identity_result.one_or_none()

                if identity is None:
                    raise DatasetLifecycleVersionNotFoundError(
                        f"Dataset version {dataset_version_id} was not found."
                    )

                dataset_id, version_attempt_id = identity

                await self._validate_publication_attempt_ownership(
                    session,
                    attempt=attempt,
                    version_attempt_id=version_attempt_id,
                )

                dataset_result = await session.execute(
                    select(DatasetORM)
                    .where(DatasetORM.dataset_id == dataset_id)
                    .with_for_update()
                )
                dataset = dataset_result.scalar_one_or_none()

                if dataset is None:
                    raise DatasetLifecycleDatasetNotFoundError(
                        f"Dataset {dataset_id} was not found."
                    )

                version_result = await session.execute(
                    select(DatasetVersionORM)
                    .where(
                        DatasetVersionORM.dataset_version_id
                        == dataset_version_id
                    )
                    .with_for_update()
                )
                version = version_result.scalar_one_or_none()

                if version is None:
                    raise DatasetLifecycleVersionNotFoundError(
                        f"Dataset version {dataset_version_id} was not found."
                    )

                if getattr(version, 'ingestion_attempt_id', None) != version_attempt_id:
                    raise IngestionJobRepositoryExecutionOwnershipError(
                        "The staging version's originating attempt changed "
                        "during publication."
                    )

                if version.status != DatasetVersionStatus.STAGING:
                    raise DatasetLifecycleInvalidVersionStateError(
                        f"Dataset version {dataset_version_id} cannot be "
                        f"published from status '{version.status}'."
                    )

                if metadata.storage_format != version.storage_format:
                    raise DatasetLifecycleInvalidPublicationMetadataError(
                        "Storage metadata format does not match the "
                        "version's declared storage format."
                    )

                if dataset.data_kind == DatasetKind.RAW_PROVIDER:
                    if (
                        metadata.record_count is not None
                        or metadata.first_timestamp is not None
                        or metadata.last_timestamp is not None
                    ):
                        raise DatasetLifecycleInvalidPublicationMetadataError(
                            "Raw provider datasets require an undefined "
                            "record count and no observation timestamps."
                        )
                else:
                    if (
                        metadata.record_count is None
                        or metadata.record_count <= 0
                        or metadata.first_timestamp is None
                        or metadata.last_timestamp is None
                    ):
                        raise DatasetLifecycleInvalidPublicationMetadataError(
                            "Normalized dataset publication requires a "
                            "positive record count and both observation "
                            "timestamps."
                        )

                published_at = datetime.now(timezone.utc)

                version.status = DatasetVersionStatus.AVAILABLE
                version.storage_uri = metadata.storage_uri
                version.checksum_algorithm = metadata.checksum_algorithm
                version.checksum = metadata.checksum
                version.record_count = metadata.record_count
                version.size_bytes = metadata.size_bytes
                version.first_timestamp = metadata.first_timestamp
                version.last_timestamp = metadata.last_timestamp
                version.published_at = published_at

                should_advance_latest = dataset.latest_version_id is None

                if dataset.latest_version_id is not None:
                    latest_result = await session.execute(
                        select(DatasetVersionORM.version_number).where(
                            DatasetVersionORM.dataset_version_id
                            == dataset.latest_version_id
                        )
                    )
                    latest_number = latest_result.scalar_one()

                    should_advance_latest = (
                        version.version_number > latest_number
                    )

                if should_advance_latest:
                    dataset.latest_version_id = version.dataset_version_id

                await session.flush()

                return self._to_domain(version)

    async def fail_staging_version(
        self,
        dataset_version_id: UUID,
        attempt: IngestionAttempt | None = None,
    ) -> DatasetVersion:
        """Mark a staging version failed while enforcing attempt ownership."""

        async with self._session_factory() as session:
            async with session.begin():
                identity_result = await session.execute(
                    select(
                        DatasetVersionORM.dataset_id,
                        DatasetVersionORM.ingestion_attempt_id,
                    ).where(
                        DatasetVersionORM.dataset_version_id
                        == dataset_version_id
                    )
                )
                identity = identity_result.one_or_none()

                if identity is None:
                    raise DatasetLifecycleVersionNotFoundError(
                        f"Dataset version {dataset_version_id} was not found."
                    )

                dataset_id, version_attempt_id = identity

                await self._validate_publication_attempt_ownership(
                    session,
                    attempt=attempt,
                    version_attempt_id=version_attempt_id,
                )

                dataset_result = await session.execute(
                    select(DatasetORM)
                    .where(DatasetORM.dataset_id == dataset_id)
                    .with_for_update()
                )
                dataset = dataset_result.scalar_one_or_none()

                if dataset is None:
                    raise DatasetLifecycleDatasetNotFoundError(
                        f"Dataset {dataset_id} was not found."
                    )

                version_result = await session.execute(
                    select(DatasetVersionORM)
                    .where(
                        DatasetVersionORM.dataset_version_id
                        == dataset_version_id
                    )
                    .with_for_update()
                )
                version = version_result.scalar_one_or_none()

                if version is None:
                    raise DatasetLifecycleVersionNotFoundError(
                        f"Dataset version {dataset_version_id} was not found."
                    )

                if getattr(version, 'ingestion_attempt_id', None) != version_attempt_id:
                    raise IngestionJobRepositoryExecutionOwnershipError(
                        "The staging version's originating attempt changed "
                        "during failure handling."
                    )

                if version.status != DatasetVersionStatus.STAGING:
                    raise DatasetLifecycleInvalidVersionStateError(
                        f"Dataset version {dataset_version_id} cannot be "
                        f"marked as failed from status '{version.status}'."
                    )

                version.status = DatasetVersionStatus.FAILED

                await session.flush()

                return self._to_domain(version)

    async def list_staging_versions_for_recovery(
        self,
        *,
        created_before: datetime,
        limit: int = 100,
    ) -> list[DatasetVersion]:
        """Find old staging versions without changing their state.

        Results are ordered deterministically by creation time, dataset,
        and version number. A stale version is only a recovery candidate;
        age alone does not prove that its writer is no longer active.
        """

        if (
            created_before.tzinfo is None
            or created_before.utcoffset() is None
        ):
            raise DatasetLifecycleInvalidRecoveryQueryError(
                "The recovery cutoff must be timezone-aware."
            )

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise DatasetLifecycleInvalidRecoveryQueryError(
                "The recovery query limit must be an integer."
            )

        if limit < 1 or limit > 1000:
            raise DatasetLifecycleInvalidRecoveryQueryError(
                "The recovery query limit must be between 1 and 1000."
            )

        cutoff = created_before.astimezone(timezone.utc)

        async with self._session_factory() as session:
            result = await session.execute(
                select(DatasetVersionORM)
                .where(
                    DatasetVersionORM.status == DatasetVersionStatus.STAGING,
                    DatasetVersionORM.created_at <= cutoff,
                )
                .order_by(
                    DatasetVersionORM.created_at.asc(),
                    DatasetVersionORM.dataset_id.asc(),
                    DatasetVersionORM.version_number.asc(),
                )
                .limit(limit)
            )

            versions = result.scalars().all()

            return [
                self._to_domain(version)
                for version in versions
            ]

    @staticmethod
    def _to_orm(version: DatasetVersion) -> DatasetVersionORM:
        """Convert a domain version into its persistence representation."""

        return DatasetVersionORM(
            dataset_version_id=version.dataset_version_id,
            dataset_id=version.dataset_id,
            version_number=version.version_number,
            schema_version=version.schema_version,
            storage_format=version.storage_format,
            status=version.status,
            first_timestamp=version.first_timestamp,
            last_timestamp=version.last_timestamp,
            record_count=version.record_count,
            size_bytes=version.size_bytes,
            storage_uri=version.storage_uri,
            checksum_algorithm=version.checksum_algorithm,
            checksum=version.checksum,
            created_at=version.created_at,
            published_at=version.published_at,
            verified_at=version.verified_at,
            ingestion_attempt_id=version.ingestion_attempt_id,
        )

    @staticmethod
    def _to_domain(version: DatasetVersionORM) -> DatasetVersion:
        """Convert a persisted version into an immutable domain snapshot."""

        return DatasetVersion(
            dataset_version_id=version.dataset_version_id,
            dataset_id=version.dataset_id,
            version_number=version.version_number,
            schema_version=version.schema_version,
            storage_format=version.storage_format,
            status=version.status,
            first_timestamp=version.first_timestamp,
            last_timestamp=version.last_timestamp,
            record_count=version.record_count,
            size_bytes=version.size_bytes,
            storage_uri=version.storage_uri,
            checksum_algorithm=version.checksum_algorithm,
            checksum=version.checksum,
            created_at=version.created_at,
            published_at=version.published_at,
            verified_at=getattr(version, "verified_at", None),
            ingestion_attempt_id=getattr(
                version, "ingestion_attempt_id", None
            ),
        )

    @staticmethod
    def _is_version_conflict(exc: IntegrityError) -> bool:
        """Identify dataset-version primary-key and numbering conflicts."""

        diagnostic = getattr(exc.orig, "diag", None)
        constraint_name = getattr(
            diagnostic,
            "constraint_name",
            None,
        )

        if constraint_name in {
            "pk_dataset_versions",
            "uq_dataset_versions_dataset_version_number",
        }:
            return True

        message = str(exc.orig).lower()

        return (
            "unique constraint failed: "
            "dataset_versions.dataset_id, "
            "dataset_versions.version_number"
        ) in message or (
            "unique constraint failed: "
            "dataset_versions.dataset_version_id"
        ) in message