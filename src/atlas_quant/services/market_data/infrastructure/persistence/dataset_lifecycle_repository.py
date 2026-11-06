"""Transactional SQLAlchemy repository for dataset lifecycle operations."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    DatasetVersion,
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version import (
    DatasetVersionORM,
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
    ) -> DatasetVersion:
        """Lock a dataset and allocate its next version in one transaction."""

        try:
            async with self._session_factory() as session:
                async with session.begin():
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

    async def publish_staging_version(
        self,
        dataset_version_id: UUID,
        metadata: StorageObjectMetadata,
    ) -> DatasetVersion:
        """Publish a verified version and advance the latest pointer atomically.

        The caller is responsible for verifying the physical storage object
        before invoking this operation.

        Dataset locking serializes publication operations for the same
        dataset. An older version may become available after a newer one,
        but it cannot move the latest-version pointer backwards.
        """

        if (
            metadata.record_count is None
            or metadata.record_count <= 0
            or metadata.first_timestamp is None
            or metadata.last_timestamp is None
        ):
            raise DatasetLifecycleInvalidPublicationMetadataError(
                "Publication requires a positive record count and both "
                "observation timestamps."
            )

        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(DatasetVersionORM.dataset_id).where(
                        DatasetVersionORM.dataset_version_id
                        == dataset_version_id
                    )
                )
                dataset_id = result.scalar_one_or_none()

                if dataset_id is None:
                    raise DatasetLifecycleVersionNotFoundError(
                        f"Dataset version {dataset_version_id} was not found."
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

                published_at = datetime.now(timezone.utc)

                version.status = DatasetVersionStatus.AVAILABLE
                version.storage_uri = metadata.storage_uri
                version.checksum_algorithm = metadata.checksum_algorithm
                version.checksum = metadata.checksum
                version.record_count = metadata.record_count
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
    ) -> DatasetVersion:
        """Mark a staging version as failed within a database transaction.

        Only STAGING versions can transition to FAILED. The dataset's latest
        published-version pointer is deliberately left unchanged.
        """

        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(DatasetVersionORM.dataset_id).where(
                        DatasetVersionORM.dataset_version_id
                        == dataset_version_id
                    )
                )
                dataset_id = result.scalar_one_or_none()

                if dataset_id is None:
                    raise DatasetLifecycleVersionNotFoundError(
                        f"Dataset version {dataset_version_id} was not found."
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
            storage_uri=version.storage_uri,
            checksum_algorithm=version.checksum_algorithm,
            checksum=version.checksum,
            created_at=version.created_at,
            published_at=version.published_at,
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
            storage_uri=version.storage_uri,
            checksum_algorithm=version.checksum_algorithm,
            checksum=version.checksum,
            created_at=version.created_at,
            published_at=version.published_at,
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