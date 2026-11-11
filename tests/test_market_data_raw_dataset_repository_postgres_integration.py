"""Opt-in PostgreSQL integration tests for raw dataset identity."""

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
    DatasetRepositoryError,
)
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
    DatasetKind,
)
from atlas_quant.services.market_data.domain.enums import AssetClass
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


def test_raw_dataset_get_or_create_postgresql():
    """Verify raw dataset identity, idempotency, and concurrency."""

    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)
    sessions = create_async_session_factory(engine)

    async def scenario():
        token = uuid4().hex
        instrument_a_id = uuid4()
        instrument_b_id = uuid4()

        provider_a = f"raw-dataset-test-a-{token}"
        provider_b = f"raw-dataset-test-b-{token}"

        repository = SQLAlchemyDatasetRepository(sessions)

        try:
            async with sessions() as session:
                async with session.begin():
                    session.add_all(
                        [
                            InstrumentORM(
                                instrument_id=instrument_a_id,
                                symbol="EURUSD",
                                asset_class=AssetClass.FOREX,
                            ),
                            InstrumentORM(
                                instrument_id=instrument_b_id,
                                symbol="GBPUSD",
                                asset_class=AssetClass.FOREX,
                            ),
                            ProviderORM(
                                provider_id=provider_a,
                                name="Raw dataset integration provider A",
                                supported_asset_classes=[
                                    AssetClass.FOREX.value
                                ],
                                supported_timeframes=[],
                                supports_historical_data=True,
                                supports_quote_data=True,
                                is_enabled=True,
                            ),
                            ProviderORM(
                                provider_id=provider_b,
                                name="Raw dataset integration provider B",
                                supported_asset_classes=[
                                    AssetClass.FOREX.value
                                ],
                                supported_timeframes=[],
                                supports_historical_data=True,
                                supports_quote_data=True,
                                is_enabled=True,
                            ),
                        ]
                    )

            # Concurrent requests for the same provider/instrument
            # identity must produce exactly one logical dataset.
            first_request = Dataset(
                dataset_id=uuid4(),
                name="EURUSD raw payload A",
                data_kind=DatasetKind.RAW_PROVIDER,
                asset_class=AssetClass.FOREX,
                instrument_id=instrument_a_id,
                instrument_symbol="EURUSD",
                provider_id=provider_a,
            )

            second_request = Dataset(
                dataset_id=uuid4(),
                name="EURUSD raw payload B",
                data_kind=DatasetKind.RAW_PROVIDER,
                asset_class=AssetClass.FOREX,
                instrument_id=instrument_a_id,
                instrument_symbol="EURUSD",
                provider_id=provider_a,
            )

            results = await asyncio.gather(
                repository.get_or_create_raw(first_request),
                repository.get_or_create_raw(second_request),
            )

            assert sum(created for _, created in results) == 1
            assert (
                results[0][0].dataset_id
                == results[1][0].dataset_id
            )

            canonical_dataset = results[0][0]

            # Repeated requests return the same canonical dataset.
            repeated, repeated_created = (
                await repository.get_or_create_raw(
                    Dataset(
                        name="EURUSD raw repeat",
                        data_kind=DatasetKind.RAW_PROVIDER,
                        asset_class=AssetClass.FOREX,
                        instrument_id=instrument_a_id,
                        instrument_symbol="EURUSD",
                        provider_id=provider_a,
                    )
                )
            )

            assert repeated_created is False
            assert repeated.dataset_id == canonical_dataset.dataset_id

            # A different provider owns a separate raw dataset.
            other_provider, other_provider_created = (
                await repository.get_or_create_raw(
                    Dataset(
                        name="EURUSD raw provider B",
                        data_kind=DatasetKind.RAW_PROVIDER,
                        asset_class=AssetClass.FOREX,
                        instrument_id=instrument_a_id,
                        instrument_symbol="EURUSD",
                        provider_id=provider_b,
                    )
                )
            )

            assert other_provider_created is True
            assert (
                other_provider.dataset_id
                != canonical_dataset.dataset_id
            )

            # A different canonical instrument is also a separate identity.
            other_instrument, other_instrument_created = (
                await repository.get_or_create_raw(
                    Dataset(
                        name="GBPUSD raw provider A",
                        data_kind=DatasetKind.RAW_PROVIDER,
                        asset_class=AssetClass.FOREX,
                        instrument_id=instrument_b_id,
                        instrument_symbol="GBPUSD",
                        provider_id=provider_a,
                    )
                )
            )

            assert other_instrument_created is True
            assert (
                other_instrument.dataset_id
                != canonical_dataset.dataset_id
            )

            # Conflicting descriptive metadata must not silently
            # reuse a canonical raw dataset.
            with pytest.raises(DatasetRepositoryError):
                await repository.get_or_create_raw(
                    Dataset(
                        name="Incorrect EURUSD identity",
                        data_kind=DatasetKind.RAW_PROVIDER,
                        asset_class=AssetClass.FOREX,
                        instrument_id=instrument_a_id,
                        instrument_symbol="GBPUSD",
                        provider_id=provider_a,
                    )
                )

            # New atomic raw datasets require canonical instrument identity.
            with pytest.raises(ValueError):
                await repository.get_or_create_raw(
                    Dataset(
                        name="Legacy raw payload",
                        data_kind=DatasetKind.RAW_PROVIDER,
                        asset_class=AssetClass.FOREX,
                        provider_id=provider_a,
                    )
                )

            # The normalized creation method must remain separate.
            with pytest.raises(ValueError):
                await repository.get_or_create(
                    Dataset(
                        name="Incorrect method",
                        data_kind=DatasetKind.RAW_PROVIDER,
                        asset_class=AssetClass.FOREX,
                        instrument_id=instrument_a_id,
                        provider_id=provider_a,
                    )
                )

            async with sessions() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(DatasetORM)
                    .where(
                        DatasetORM.data_kind
                        == DatasetKind.RAW_PROVIDER,
                        DatasetORM.instrument_id.in_(
                            [instrument_a_id, instrument_b_id]
                        ),
                    )
                )

            assert count == 3

        finally:
            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        delete(DatasetORM).where(
                            DatasetORM.instrument_id.in_(
                                [instrument_a_id, instrument_b_id]
                            )
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
                            InstrumentORM.instrument_id.in_(
                                [instrument_a_id, instrument_b_id]
                            )
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