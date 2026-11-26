
"""Asynchronous SQLAlchemy repository for immutable dataset versions."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.market_data.application.repository_errors import (
    DatasetVersionRepositoryConflictError,
    DatasetVersionRepositoryDatasetNotFoundError,
)
from atlas_quant.services.market_data.domain.datasets import DatasetVersion
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version import (
    DatasetVersionORM,
)


class SQLAlchemyDatasetVersionRepository:
    """Persist immutable dataset versions using async SQLAlchemy."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get_by_id(
        self,
        version_id: UUID,
    ) -> DatasetVersion | None:
        """Retrieve a version by its permanent UUID."""

        async with self._session_factory() as session:
            orm_version = await session.get(
                DatasetVersionORM,
                version_id,
            )

            if orm_version is None:
                return None

            return self._to_domain(orm_version)

    async def get_by_version_number(
        self,
        dataset_id: UUID,
        version_number: int,
    ) -> DatasetVersion | None:
        """Retrieve a version by its dataset and version number."""

        statement = select(DatasetVersionORM).where(
            DatasetVersionORM.dataset_id == dataset_id,
            DatasetVersionORM.version_number == version_number,
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            orm_version = result.scalar_one_or_none()

        if orm_version is None:
            return None

        return self._to_domain(orm_version)

    async def list_by_dataset_id(
        self,
        dataset_id: UUID,
    ) -> tuple[DatasetVersion, ...]:
        """Retrieve versions in ascending version-number order."""

        statement = (
            select(DatasetVersionORM)
            .where(DatasetVersionORM.dataset_id == dataset_id)
            .order_by(DatasetVersionORM.version_number)
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            orm_versions = result.scalars().all()

        return tuple(
            self._to_domain(item)
            for item in orm_versions
        )

    async def add(
        self,
        version: DatasetVersion,
    ) -> None:
        """Insert a new immutable dataset version."""

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    dataset = await session.get(
                        DatasetORM,
                        version.dataset_id,
                    )

                    if dataset is None:
                        raise DatasetVersionRepositoryDatasetNotFoundError(
                            f"Dataset {version.dataset_id} was not found."
                        )

                    session.add(self._to_orm(version))
                    await session.flush()

        except IntegrityError as exc:
            if self._is_version_conflict(exc):
                raise DatasetVersionRepositoryConflictError(
                    "The dataset version identifier or version number "
                    "already exists."
                ) from exc

            raise

    @staticmethod
    def _to_domain(
        orm_version: DatasetVersionORM,
    ) -> DatasetVersion:
        """Convert a persistence model into a domain dataset version."""

        return DatasetVersion(
            dataset_version_id=orm_version.dataset_version_id,
            dataset_id=orm_version.dataset_id,
            version_number=orm_version.version_number,
            schema_version=orm_version.schema_version,
            storage_format=orm_version.storage_format,
            status=orm_version.status,
            first_timestamp=SQLAlchemyDatasetVersionRepository._as_utc(
                orm_version.first_timestamp
            ),
            last_timestamp=SQLAlchemyDatasetVersionRepository._as_utc(
                orm_version.last_timestamp
            ),
            record_count=orm_version.record_count,
            size_bytes=orm_version.size_bytes,
            storage_uri=orm_version.storage_uri,
            checksum_algorithm=orm_version.checksum_algorithm,
            checksum=orm_version.checksum,
            created_at=SQLAlchemyDatasetVersionRepository._as_utc(
                orm_version.created_at
            ),
            published_at=SQLAlchemyDatasetVersionRepository._as_utc(
                orm_version.published_at
            ),
            verified_at=SQLAlchemyDatasetVersionRepository._as_utc(
                orm_version.verified_at
            ),
            ingestion_attempt_id=orm_version.ingestion_attempt_id,
        )

    @staticmethod
    def _to_orm(
        version: DatasetVersion,
    ) -> DatasetVersionORM:
        """Convert a domain dataset version into a persistence model."""

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
    def _as_utc(
        value: datetime | None,
    ) -> datetime | None:
        """Normalize database timestamps to timezone-aware UTC.

        SQLite may return naive datetimes for timezone-aware columns.
        Values written by the domain are UTC, so naive SQLite results
        are interpreted as UTC.
        """

        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)

    @staticmethod
    def _is_version_conflict(
        exc: IntegrityError,
    ) -> bool:
        """Identify dataset-version primary or numbering conflicts."""

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