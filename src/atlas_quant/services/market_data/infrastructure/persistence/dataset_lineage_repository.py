"""SQLAlchemy repository for immutable dataset-version lineage."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.market_data.application.repository_errors import (
    DatasetVersionLineageRepositoryConflictError,
    DatasetVersionLineageRepositoryIntegrityError,
    DatasetVersionLineageRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.dataset_lineage import (
    DatasetLineageRelationship,
    DatasetLineageSourceType,
    DatasetVersionLineage,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_lineage import (
    DatasetVersionLineageORM,
)


class SQLAlchemyDatasetVersionLineageRepository:
    """Persist and retrieve immutable dataset lineage relationships."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get_by_id(
        self,
        lineage_id: UUID,
    ) -> DatasetVersionLineage | None:
        async with self._session_factory() as session:
            record = await session.get(
                DatasetVersionLineageORM,
                lineage_id,
            )

            return self._to_domain(record) if record is not None else None

    async def list_by_target_version_id(
        self,
        target_dataset_version_id: UUID,
    ) -> tuple[DatasetVersionLineage, ...]:
        statement = (
            select(DatasetVersionLineageORM)
            .where(
                DatasetVersionLineageORM.target_dataset_version_id
                == target_dataset_version_id
            )
            .order_by(DatasetVersionLineageORM.lineage_id)
        )

        async with self._session_factory() as session:
            result = await session.scalars(statement)
            records = result.all()

            return tuple(self._to_domain(record) for record in records)

    async def list_by_source(
        self,
        source_type: DatasetLineageSourceType,
        source_id: UUID,
    ) -> tuple[DatasetVersionLineage, ...]:
        if source_type == DatasetLineageSourceType.INGESTION_JOB:
            source_column = DatasetVersionLineageORM.ingestion_job_id
        elif source_type == DatasetLineageSourceType.DATASET_VERSION:
            source_column = (
                DatasetVersionLineageORM.source_dataset_version_id
            )
        else:
            raise ValueError("Unsupported dataset lineage source type.")

        statement = (
            select(DatasetVersionLineageORM)
            .where(
                DatasetVersionLineageORM.source_type == source_type,
                source_column == source_id,
            )
            .order_by(
                DatasetVersionLineageORM.target_dataset_version_id,
                DatasetVersionLineageORM.lineage_id,
            )
        )

        async with self._session_factory() as session:
            result = await session.scalars(statement)
            records = result.all()

            return tuple(self._to_domain(record) for record in records)

    async def add(
        self,
        lineage: DatasetVersionLineage,
    ) -> None:
        async with self._session_factory() as session:
            try:
                async with session.begin():
                    existing = await session.get(
                        DatasetVersionLineageORM,
                        lineage.lineage_id,
                    )

                    if existing is not None:
                        raise DatasetVersionLineageRepositoryConflictError(
                            f"Lineage '{lineage.lineage_id}' already exists."
                        )

                    record = self._to_orm(lineage)
                    session.add(record)

                    await session.flush()

            except IntegrityError as exc:
                raise DatasetVersionLineageRepositoryIntegrityError(
                    "Dataset lineage violates a database constraint."
                ) from exc

    @staticmethod
    def _to_domain(
        record: DatasetVersionLineageORM,
    ) -> DatasetVersionLineage:
        if record.source_type == DatasetLineageSourceType.INGESTION_JOB:
            source_id = record.ingestion_job_id
        else:
            source_id = record.source_dataset_version_id

        if source_id is None:
            raise DatasetVersionLineageRepositoryIntegrityError(
                "Persisted lineage has no matching source identifier."
            )

        return DatasetVersionLineage(
            lineage_id=record.lineage_id,
            target_dataset_version_id=record.target_dataset_version_id,
            source_type=record.source_type,
            source_id=source_id,
            relationship=record.relationship_type,
        )

    @staticmethod
    def _to_orm(
        lineage: DatasetVersionLineage,
    ) -> DatasetVersionLineageORM:
        ingestion_job_id = None
        source_dataset_version_id = None

        if lineage.source_type == DatasetLineageSourceType.INGESTION_JOB:
            ingestion_job_id = lineage.source_id
        elif (
            lineage.source_type
            == DatasetLineageSourceType.DATASET_VERSION
        ):
            source_dataset_version_id = lineage.source_id
        else:
            raise ValueError("Unsupported dataset lineage source type.")

        return DatasetVersionLineageORM(
            lineage_id=lineage.lineage_id,
            target_dataset_version_id=lineage.target_dataset_version_id,
            source_type=lineage.source_type,
            ingestion_job_id=ingestion_job_id,
            source_dataset_version_id=source_dataset_version_id,
            relationship_type=lineage.relationship,
        )