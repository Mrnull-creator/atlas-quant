"""Verify dataset repositories against PostgreSQL.

All test records are rolled back when the script finishes.
"""

import asyncio
import selectors
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.platform.database import create_async_engine_from_settings
from atlas_quant.services.market_data.application.repository_errors import (
    DatasetRepositoryDuplicateError,
    DatasetVersionRepositoryConflictError,
)
from atlas_quant.services.market_data.domain.datasets import (
    AssetClass,
    Dataset,
    DatasetKind,
    DatasetVersion,
    DatasetVersionStatus,
    Timeframe,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider import (
    ProviderORM,  # noqa: F401 - registers the provider table.
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_repository import (
    SQLAlchemyDatasetRepository,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version_repository import (
    SQLAlchemyDatasetVersionRepository,
)


def make_dataset(name: str) -> Dataset:
    return Dataset(
        dataset_id=uuid4(),
        name=name,
        data_kind=DatasetKind.OHLCV,
        asset_class=AssetClass.FOREX,
        instrument_symbol="EURUSD",
        timeframe=Timeframe.H1,
    )


def make_version(dataset_id, version_number: int) -> DatasetVersion:
    return DatasetVersion(
        dataset_version_id=uuid4(),
        dataset_id=dataset_id,
        version_number=version_number,
        schema_version="1",
        storage_format="parquet",
        status=DatasetVersionStatus.STAGING,
        record_count=0,
    )


async def main() -> None:
    engine = create_async_engine_from_settings()

    try:
        async with engine.connect() as connection:
            outer_transaction = await connection.begin()

            session_factory = async_sessionmaker(
                bind=connection,
                class_=AsyncSession,
                autoflush=False,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )

            datasets = SQLAlchemyDatasetRepository(session_factory)
            versions = SQLAlchemyDatasetVersionRepository(
                session_factory
            )

            try:
                # 1. Create two independent datasets.
                first_dataset = make_dataset(
                    "PostgreSQL integration test A"
                )
                second_dataset = make_dataset(
                    "PostgreSQL integration test B"
                )

                await datasets.add(first_dataset)
                await datasets.add(second_dataset)
                print("PASS: Dataset creation")

                # 2. Retrieve datasets.
                retrieved = await datasets.get_by_id(
                    first_dataset.dataset_id
                )
                assert retrieved is not None
                assert retrieved.dataset_id == first_dataset.dataset_id

                listed = await datasets.list_all()
                assert first_dataset.dataset_id in {
                    item.dataset_id for item in listed
                }
                print("PASS: Dataset retrieval and listing")

                # 3. Create versions belonging to each dataset.
                first_version = make_version(
                    first_dataset.dataset_id, 1
                )
                second_version = make_version(
                    second_dataset.dataset_id, 1
                )

                await versions.add(first_version)
                await versions.add(second_version)
                print("PASS: Dataset version creation")

                # 4. Retrieve versions by ID and version number.
                retrieved_version = await versions.get_by_id(
                    first_version.dataset_version_id
                )
                assert retrieved_version is not None
                assert (
                    retrieved_version.dataset_version_id
                    == first_version.dataset_version_id
                )

                by_number = await versions.get_by_version_number(
                    first_dataset.dataset_id, 1
                )
                assert by_number is not None
                assert (
                    by_number.dataset_version_id
                    == first_version.dataset_version_id
                )

                history = await versions.list_by_dataset_id(
                    first_dataset.dataset_id
                )
                assert len(history) == 1
                print("PASS: Dataset version retrieval and history")

                # 5. Update the dataset's latest-version pointer.
                updated_dataset = Dataset(
                    dataset_id=first_dataset.dataset_id,
                    name="PostgreSQL integration test A updated",
                    data_kind=first_dataset.data_kind,
                    asset_class=first_dataset.asset_class,
                    instrument_symbol=first_dataset.instrument_symbol,
                    provider_id=first_dataset.provider_id,
                    timeframe=first_dataset.timeframe,
                    latest_version_id=first_version.dataset_version_id,
                )

                await datasets.update(updated_dataset)

                updated = await datasets.get_by_id(
                    first_dataset.dataset_id
                )
                assert updated is not None
                assert (
                    updated.latest_version_id
                    == first_version.dataset_version_id
                )
                print("PASS: Latest-version pointer update")

                # 6. Reject duplicate version numbers.
                duplicate = make_version(
                    first_dataset.dataset_id, 1
                )

                try:
                    await versions.add(duplicate)
                except DatasetVersionRepositoryConflictError:
                    print("PASS: Duplicate version number rejected")
                else:
                    raise AssertionError(
                        "Duplicate dataset version was accepted."
                    )

                # 7. Reject a latest-version pointer belonging
                #    to another dataset.
                invalid_update = Dataset(
                    dataset_id=first_dataset.dataset_id,
                    name=updated_dataset.name,
                    data_kind=updated_dataset.data_kind,
                    asset_class=updated_dataset.asset_class,
                    instrument_symbol=updated_dataset.instrument_symbol,
                    provider_id=updated_dataset.provider_id,
                    timeframe=updated_dataset.timeframe,
                    latest_version_id=second_version.dataset_version_id,
                )

                try:
                    await datasets.update(invalid_update)
                except IntegrityError:
                    print(
                        "PASS: Cross-dataset version reference rejected"
                    )
                else:
                    raise AssertionError(
                        "Cross-dataset version reference was accepted."
                    )

                # Confirm the failed update did not change the pointer.
                unchanged = await datasets.get_by_id(
                    first_dataset.dataset_id
                )
                assert unchanged is not None
                assert (
                    unchanged.latest_version_id
                    == first_version.dataset_version_id
                )
                print("PASS: Failed update rolled back safely")

                # 8. Reject duplicate dataset identities.
                try:
                    await datasets.add(first_dataset)
                except DatasetRepositoryDuplicateError:
                    print("PASS: Duplicate dataset identity rejected")
                else:
                    raise AssertionError(
                        "Duplicate dataset identity was accepted."
                    )

                print("\nAll PostgreSQL integration checks passed.")

            finally:
                # Undo every insert and update made by this test.
                if outer_transaction.is_active:
                    await outer_transaction.rollback()

                print("Test transaction rolled back.")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(
        main(),
        loop_factory=lambda: asyncio.SelectorEventLoop(
            selectors.SelectSelector()
        ),
    )