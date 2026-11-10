"""Opt-in PostgreSQL integration tests for dataset get-or-create."""

import asyncio
import os
import selectors
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from atlas_quant.platform.config import get_settings
from atlas_quant.platform.database import (
    create_async_database_engine,
    create_async_session_factory,
)
from atlas_quant.services.market_data.application.repository_errors import (
    DatasetRepositoryDuplicateError,
    DatasetRepositoryError,
)
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
    DatasetKind,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_repository import (
    SQLAlchemyDatasetRepository,
)
from atlas_quant.services.market_data.infrastructure.persistence.instrument import (
    InstrumentORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider import (
    ProviderORM,
)


pytestmark = pytest.mark.skipif(
    os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") != "1",
    reason=(
        "Set ATLAS_RUN_POSTGRES_INTEGRATION=1 "
        "to run PostgreSQL integration tests."
    ),
)


def test_dataset_get_or_create_postgresql():
    """Verify dataset identity, idempotency, and concurrent creation."""

    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)
    sessions = create_async_session_factory(engine)

    async def scenario():
        token = uuid4().hex
        instrument_id = uuid4()

        provider_a = f"dataset-test-a-{token}"
        provider_b = f"dataset-test-b-{token}"

        repository = SQLAlchemyDatasetRepository(sessions)

        try:
            async with sessions() as session:
                async with session.begin():
                    session.add(
                        InstrumentORM(
                            instrument_id=instrument_id,
                            symbol="EURUSD",
                            asset_class=AssetClass.FOREX,
                        )
                    )

                    session.add_all(
                        [
                            ProviderORM(
                                provider_id=provider_a,
                                name="Dataset integration provider A",
                                supported_asset_classes=[
                                    AssetClass.FOREX.value
                                ],
                                supported_timeframes=[
                                    Timeframe.M1.value,
                                    Timeframe.H1.value,
                                ],
                                supports_historical_data=True,
                                supports_quote_data=True,
                                is_enabled=True,
                            ),
                            ProviderORM(
                                provider_id=provider_b,
                                name="Dataset integration provider B",
                                supported_asset_classes=[
                                    AssetClass.FOREX.value
                                ],
                                supported_timeframes=[
                                    Timeframe.M1.value,
                                    Timeframe.H1.value,
                                ],
                                supports_historical_data=True,
                                supports_quote_data=True,
                                is_enabled=True,
                            ),
                        ]
                    )

            # Two independent requests attempt to create the same
            # OHLCV identity at the same time.
            first_request = Dataset(
                dataset_id=uuid4(),
                instrument_id=instrument_id,
                name="EURUSD one-minute provider A",
                data_kind=DatasetKind.OHLCV,
                asset_class=AssetClass.FOREX,
                instrument_symbol="EURUSD",
                provider_id=provider_a,
                timeframe=Timeframe.M1,
            )

            second_request = Dataset(
                dataset_id=uuid4(),
                instrument_id=instrument_id,
                name="EURUSD one-minute provider B",
                data_kind=DatasetKind.OHLCV,
                asset_class=AssetClass.FOREX,
                instrument_symbol="EURUSD",
                provider_id=provider_b,
                timeframe=Timeframe.M1,
            )

            results = await asyncio.gather(
                repository.get_or_create(first_request),
                repository.get_or_create(second_request),
            )

            first_result, second_result = results

            assert sum(created for _, created in results) == 1
            assert (
                first_result[0].dataset_id
                == second_result[0].dataset_id
            )

            # A later request from the other provider is idempotent.
            repeated_dataset, repeated_created = (
                await repository.get_or_create(
                    Dataset(
                        dataset_id=uuid4(),
                        instrument_id=instrument_id,
                        name="EURUSD repeated request",
                        data_kind=DatasetKind.OHLCV,
                        asset_class=AssetClass.FOREX,
                        instrument_symbol="EURUSD",
                        provider_id=provider_b,
                        timeframe=Timeframe.M1,
                    )
                )
            )

            assert repeated_created is False
            assert (
                repeated_dataset.dataset_id
                == first_result[0].dataset_id
            )

            # Reusing an existing primary key for a different
            # logical identity must raise the repository exception.
            with pytest.raises(DatasetRepositoryDuplicateError):
                await repository.get_or_create(
                    Dataset(
                        dataset_id=first_result[0].dataset_id,
                        instrument_id=instrument_id,
                        name="Conflicting dataset ID",
                        data_kind=DatasetKind.OHLCV,
                        asset_class=AssetClass.FOREX,
                        instrument_symbol="EURUSD",
                        provider_id=provider_a,
                        timeframe=Timeframe.H1,
                    )
                )

            # A different OHLCV timeframe is a different identity.
            hourly_dataset, hourly_created = (
                await repository.get_or_create(
                    Dataset(
                        instrument_id=instrument_id,
                        name="EURUSD hourly",
                        data_kind=DatasetKind.OHLCV,
                        asset_class=AssetClass.FOREX,
                        instrument_symbol="EURUSD",
                        provider_id=provider_a,
                        timeframe=Timeframe.H1,
                    )
                )
            )

            assert hourly_created is True
            assert (
                hourly_dataset.dataset_id
                != repeated_dataset.dataset_id
            )

            # BID_ASK identity is one dataset per instrument.
            quote_dataset, quote_created = (
                await repository.get_or_create(
                    Dataset(
                        instrument_id=instrument_id,
                        name="EURUSD quotes",
                        data_kind=DatasetKind.BID_ASK,
                        asset_class=AssetClass.FOREX,
                        instrument_symbol="EURUSD",
                        provider_id=provider_a,
                    )
                )
            )

            assert quote_created is True
            assert quote_dataset.timeframe is None

            repeated_quote, quote_repeated_created = (
                await repository.get_or_create(
                    Dataset(
                        instrument_id=instrument_id,
                        name="EURUSD quotes from provider B",
                        data_kind=DatasetKind.BID_ASK,
                        asset_class=AssetClass.FOREX,
                        instrument_symbol="EURUSD",
                        provider_id=provider_b,
                    )
                )
            )

            assert quote_repeated_created is False
            assert repeated_quote.dataset_id == quote_dataset.dataset_id

            # A canonical UUID cannot be reused with contradictory
            # descriptive instrument metadata.
            with pytest.raises(DatasetRepositoryError):
                await repository.get_or_create(
                    Dataset(
                        instrument_id=instrument_id,
                        name="Incorrect instrument metadata",
                        data_kind=DatasetKind.OHLCV,
                        asset_class=AssetClass.FOREX,
                        instrument_symbol="GBPUSD",
                        provider_id=provider_a,
                        timeframe=Timeframe.M1,
                    )
                )

            # Raw provider datasets use a separate identity policy.
            with pytest.raises(ValueError):
                await repository.get_or_create(
                    Dataset(
                        name="Raw provider payload",
                        data_kind=DatasetKind.RAW_PROVIDER,
                        asset_class=AssetClass.FOREX,
                        provider_id=provider_a,
                    )
                )

            # Three logical datasets exist: M1, H1, and BID_ASK.
            async with sessions() as session:
                count_statement = (
                    select(func.count())
                    .select_from(DatasetORM)
                    .where(
                        DatasetORM.instrument_id == instrument_id
                    )
                )

                count = await session.scalar(count_statement)

            assert count == 3

        finally:
            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        delete(DatasetORM).where(
                            DatasetORM.instrument_id == instrument_id
                        )
                    )

                    await session.execute(
                        delete(ProviderORM).where(
                            ProviderORM.provider_id.in_(
                                [provider_a, provider_b]
                            )
                        )
                    )

                    await session.execute(
                        delete(InstrumentORM).where(
                            InstrumentORM.instrument_id == instrument_id
                        )
                    )

    try:
        asyncio.run(
            scenario(),
            loop_factory=lambda: asyncio.SelectorEventLoop(
                selectors.SelectSelector()
            ),
        )
    finally:
        asyncio.run(
            engine.dispose(),
            loop_factory=lambda: asyncio.SelectorEventLoop(
                selectors.SelectSelector()
            ),
        )