"""Asynchronous SQLAlchemy repository for market data datasets."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.market_data.application.repository_errors import (
    DatasetRepositoryDuplicateError,
    DatasetRepositoryError,
    DatasetRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
    DatasetKind,
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

    async def get_or_create(
        self,
        dataset: Dataset,
    ) -> tuple[Dataset, bool]:
        """Atomically retrieve or create a normalized dataset.

        OHLCV identity is (instrument_id, timeframe).
        BID_ASK identity is (instrument_id).

        Returns (canonical_dataset, created).
        """

        if dataset.data_kind == DatasetKind.RAW_PROVIDER:
            raise ValueError(
                "get_or_create supports normalized datasets only; "
                "use get_or_create_raw for raw provider datasets."
            )

        if dataset.data_kind == DatasetKind.OHLCV:
            conflict_columns = [
                DatasetORM.instrument_id,
                DatasetORM.timeframe,
            ]
            conflict_predicate = text("data_kind = 'ohlcv'")
            identity_conditions = (
                DatasetORM.data_kind == DatasetKind.OHLCV,
                DatasetORM.instrument_id == dataset.instrument_id,
                DatasetORM.timeframe == dataset.timeframe,
            )

        elif dataset.data_kind == DatasetKind.BID_ASK:
            conflict_columns = [
                DatasetORM.instrument_id,
            ]
            conflict_predicate = text("data_kind = 'bid_ask'")
            identity_conditions = (
                DatasetORM.data_kind == DatasetKind.BID_ASK,
                DatasetORM.instrument_id == dataset.instrument_id,
            )

        else:
            raise ValueError(
                f"Unsupported normalized dataset kind: {dataset.data_kind!r}."
            )

        orm_dataset = self._to_orm(dataset)

        statement = (
            pg_insert(DatasetORM)
            .values(
                dataset_id=orm_dataset.dataset_id,
                instrument_id=orm_dataset.instrument_id,
                name=orm_dataset.name,
                data_kind=orm_dataset.data_kind,
                asset_class=orm_dataset.asset_class,
                instrument_symbol=orm_dataset.instrument_symbol,
                provider_id=orm_dataset.provider_id,
                timeframe=orm_dataset.timeframe,
                latest_version_id=orm_dataset.latest_version_id,
            )
            .on_conflict_do_nothing(
                index_elements=conflict_columns,
                index_where=conflict_predicate,
            )
            .returning(DatasetORM.dataset_id)
        )

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    result = await session.execute(statement)
                    inserted_id = result.scalar_one_or_none()

                    if inserted_id is not None:
                        return dataset, True

                    existing_statement = select(DatasetORM).where(
                        *identity_conditions
                    )

                    existing_result = await session.execute(
                        existing_statement
                    )
                    existing = existing_result.scalar_one_or_none()

                    if existing is not None:
                        self._validate_existing_instrument_identity(
                            requested=dataset,
                            existing=existing,
                        )
                        return self._to_domain(existing), False

                    raise DatasetRepositoryError(
                        "Dataset creation encountered a uniqueness "
                        "conflict, but the canonical dataset could not "
                        "be retrieved. Retry the operation."
                    )

        except IntegrityError as exc:
            if self._is_primary_key_collision(exc):
                raise DatasetRepositoryDuplicateError(
                    f"Dataset ID {dataset.dataset_id} already exists."
                ) from exc

            raise

    async def get_or_create_raw(
        self,
        dataset: Dataset,
    ) -> tuple[Dataset, bool]:
        """Atomically retrieve or create a raw provider dataset.

        Raw dataset identity is (provider_id, instrument_id).
        The canonical instrument UUID is required. Provider mapping
        identity is deliberately retained by the ingestion job instead.

        Returns (canonical_dataset, created).
        """

        if dataset.data_kind != DatasetKind.RAW_PROVIDER:
            raise ValueError(
                "get_or_create_raw supports RAW_PROVIDER datasets only."
            )

        if not dataset.provider_id:
            raise ValueError(
                "Raw provider datasets require a provider_id."
            )

        if dataset.instrument_id is None:
            raise ValueError(
                "New raw provider datasets require a canonical instrument_id."
            )

        orm_dataset = self._to_orm(dataset)

        statement = (
            pg_insert(DatasetORM)
            .values(
                dataset_id=orm_dataset.dataset_id,
                instrument_id=orm_dataset.instrument_id,
                name=orm_dataset.name,
                data_kind=orm_dataset.data_kind,
                asset_class=orm_dataset.asset_class,
                instrument_symbol=orm_dataset.instrument_symbol,
                provider_id=orm_dataset.provider_id,
                timeframe=orm_dataset.timeframe,
                latest_version_id=orm_dataset.latest_version_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    DatasetORM.provider_id,
                    DatasetORM.instrument_id,
                ],
                index_where=text(
                    "data_kind = 'raw_provider' "
                    "AND instrument_id IS NOT NULL"
                ),
            )
            .returning(DatasetORM.dataset_id)
        )

        identity_conditions = (
            DatasetORM.data_kind == DatasetKind.RAW_PROVIDER,
            DatasetORM.provider_id == dataset.provider_id,
            DatasetORM.instrument_id == dataset.instrument_id,
        )

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    result = await session.execute(statement)
                    inserted_id = result.scalar_one_or_none()

                    if inserted_id is not None:
                        return dataset, True

                    existing_statement = select(DatasetORM).where(
                        *identity_conditions
                    )

                    existing_result = await session.execute(
                        existing_statement
                    )
                    existing = existing_result.scalar_one_or_none()

                    if existing is not None:
                        self._validate_existing_raw_identity(
                            requested=dataset,
                            existing=existing,
                        )
                        return self._to_domain(existing), False

                    raise DatasetRepositoryError(
                        "Raw dataset creation encountered a uniqueness "
                        "conflict, but the canonical dataset could not "
                        "be retrieved. Retry the operation."
                    )

        except IntegrityError as exc:
            if self._is_primary_key_collision(exc):
                raise DatasetRepositoryDuplicateError(
                    f"Dataset ID {dataset.dataset_id} already exists."
                ) from exc

            raise

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
                    f"Dataset {dataset.dataset_id} or its logical identity "
                    "already exists."
                ) from exc

            raise

    async def update(
        self,
        dataset: Dataset,
    ) -> None:
        """Update the name and latest-version pointer only."""

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
    def _validate_existing_instrument_identity(
        *,
        requested: Dataset,
        existing: DatasetORM,
    ) -> None:
        """Ensure descriptive instrument metadata matches canonical identity."""

        if (
            existing.instrument_id != requested.instrument_id
            or existing.instrument_symbol != requested.instrument_symbol
            or existing.asset_class != requested.asset_class
        ):
            raise DatasetRepositoryError(
                "The existing normalized dataset has instrument metadata "
                "that conflicts with the requested canonical instrument "
                f"identity {requested.instrument_id}."
            )

    @staticmethod
    def _validate_existing_raw_identity(
        *,
        requested: Dataset,
        existing: DatasetORM,
    ) -> None:
        """Ensure raw dataset metadata agrees with its canonical identity."""

        if (
            existing.provider_id != requested.provider_id
            or existing.instrument_id != requested.instrument_id
            or existing.asset_class != requested.asset_class
            or (
                existing.instrument_symbol is not None
                and requested.instrument_symbol is not None
                and existing.instrument_symbol
                != requested.instrument_symbol
            )
        ):
            raise DatasetRepositoryError(
                "The existing raw provider dataset has metadata that "
                "conflicts with the requested provider/instrument identity "
                f"({requested.provider_id}, {requested.instrument_id})."
            )

    @staticmethod
    def _to_domain(
        orm_dataset: DatasetORM,
    ) -> Dataset:
        """Convert a persistence model into a domain dataset."""

        return Dataset(
            dataset_id=orm_dataset.dataset_id,
            instrument_id=orm_dataset.instrument_id,
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
            instrument_id=dataset.instrument_id,
            name=dataset.name,
            data_kind=dataset.data_kind,
            asset_class=dataset.asset_class,
            instrument_symbol=dataset.instrument_symbol,
            provider_id=dataset.provider_id,
            timeframe=dataset.timeframe,
            latest_version_id=dataset.latest_version_id,
        )

    @staticmethod
    def _is_primary_key_collision(
        exc: IntegrityError,
    ) -> bool:
        """Identify a collision on the dataset primary key."""

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

    @classmethod
    def _is_duplicate_dataset(
        cls,
        exc: IntegrityError,
    ) -> bool:
        """Identify primary-key and logical-identity conflicts."""

        if cls._is_primary_key_collision(exc):
            return True

        diagnostic = getattr(exc.orig, "diag", None)
        constraint_name = getattr(
            diagnostic,
            "constraint_name",
            None,
        )

        return constraint_name in {
            "uq_datasets_ohlcv_instrument_timeframe",
            "uq_datasets_bid_ask_instrument",
            "uq_datasets_raw_provider_instrument",
        }