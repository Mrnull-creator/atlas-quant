"""Asynchronous SQLAlchemy repository for market data datasets."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.market_data.application.repository_errors import (
    DatasetRepositoryDuplicateError,
    DatasetRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)


class SQLAlchemyDatasetRepository:
    """Persist logical dataset identities using async SQLAlchemy."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get_by_id(
        self,
        dataset_id: UUID,
    ) -> Dataset | None:
        """Retrieve a dataset by its permanent UUID."""

        async with self._session_factory() as session:
            orm_dataset = await session.get(
                DatasetORM,
                dataset_id,
            )

            if orm_dataset is None:
                return None

            return self._to_domain(orm_dataset)

    async def list_all(self) -> tuple[Dataset, ...]:
        """Retrieve datasets in deterministic UUID order."""

        statement = select(DatasetORM).order_by(
            DatasetORM.dataset_id
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            orm_datasets = result.scalars().all()

        return tuple(
            self._to_domain(item)
            for item in orm_datasets
        )

    async def add(
        self,
        dataset: Dataset,
    ) -> None:
        """Insert a new logical dataset."""

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(self._to_orm(dataset))
                    await session.flush()

        except IntegrityError as exc:
            if self._is_duplicate_dataset(exc):
                raise DatasetRepositoryDuplicateError(
                    f"Dataset {dataset.dataset_id} already exists."
                ) from exc

            raise

    async def update(
        self,
        dataset: Dataset,
    ) -> None:
        """Update the name and latest-version pointer only.

        Dataset identity fields remain immutable after creation.
        The composite database foreign key ensures that a latest
        version belongs to this dataset.
        """

        async with self._session_factory() as session:
            async with session.begin():
                orm_dataset = await session.get(
                    DatasetORM,
                    dataset.dataset_id,
                )

                if orm_dataset is None:
                    raise DatasetRepositoryRecordNotFoundError(
                        f"Dataset {dataset.dataset_id} was not found."
                    )

                orm_dataset.name = dataset.name
                orm_dataset.latest_version_id = (
                    dataset.latest_version_id
                )

                await session.flush()

    @staticmethod
    def _to_domain(
        orm_dataset: DatasetORM,
    ) -> Dataset:
        """Convert a persistence model into a domain dataset."""

        return Dataset(
            dataset_id=orm_dataset.dataset_id,
            name=orm_dataset.name,
            data_kind=orm_dataset.data_kind,
            asset_class=orm_dataset.asset_class,
            instrument_symbol=orm_dataset.instrument_symbol,
            provider_id=orm_dataset.provider_id,
            timeframe=orm_dataset.timeframe,
            latest_version_id=orm_dataset.latest_version_id,
        )

    @staticmethod
    def _to_orm(
        dataset: Dataset,
    ) -> DatasetORM:
        """Convert a domain dataset into a persistence model."""

        return DatasetORM(
            dataset_id=dataset.dataset_id,
            name=dataset.name,
            data_kind=dataset.data_kind,
            asset_class=dataset.asset_class,
            instrument_symbol=dataset.instrument_symbol,
            provider_id=dataset.provider_id,
            timeframe=dataset.timeframe,
            latest_version_id=dataset.latest_version_id,
        )

    @staticmethod
    def _is_duplicate_dataset(
        exc: IntegrityError,
    ) -> bool:
        """Identify only dataset primary-key conflicts."""

        diagnostic = getattr(exc.orig, "diag", None)
        constraint_name = getattr(
            diagnostic,
            "constraint_name",
            None,
        )

        if constraint_name == "pk_datasets":
            return True

        message = str(exc.orig).lower()

        return (
            "unique constraint failed: datasets.dataset_id" in message
            or "primary key constraint failed: datasets.dataset_id"
            in message
        )