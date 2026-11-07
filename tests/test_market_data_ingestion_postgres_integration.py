"""Opt-in PostgreSQL integration test for ingestion persistence."""

import asyncio
import os
import selectors
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from atlas_quant.platform.config import get_settings
from atlas_quant.platform.database import (
    create_async_database_engine,
    create_async_session_factory,
)
from atlas_quant.services.market_data.domain.dataset_lineage import (
    DatasetLineageRelationship,
    DatasetLineageSourceType,
    DatasetVersionLineage,
)
from atlas_quant.services.market_data.domain.datasets import (
    DatasetKind,
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.ingestion import IngestionJob
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_lineage import (
    DatasetVersionLineageORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version import (
    DatasetVersionORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_job import (
    IngestionJobDatasetVersionORM,
    IngestionJobORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_lineage_repository import (
    SQLAlchemyDatasetVersionLineageRepository,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_job_repository import (
    SQLAlchemyIngestionJobRepository,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider import (
    ProviderORM,
)


pytestmark = pytest.mark.skipif(
    os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") != "1",
    reason="Set ATLAS_RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL integration tests.",
)


def test_ingestion_job_and_dataset_lineage_postgresql_round_trip():
    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)
    sessions = create_async_session_factory(engine)

    async def scenario():
        token = uuid4().hex
        provider_id = f"postgres-integration-{token}"
        dataset_id = uuid4()
        version_id = uuid4()
        job_id = uuid4()
        lineage_id = uuid4()

        now = datetime.now(timezone.utc)
        requested_start = now - timedelta(days=1)

        job = IngestionJob(
            job_id=job_id,
            provider_id=provider_id,
            asset_class=AssetClass.FOREX,
            instrument_symbol="EURUSD",
            timeframe=Timeframe.M1,
            requested_start=requested_start,
            requested_end=now,
        )

        lineage = DatasetVersionLineage(
            lineage_id=lineage_id,
            target_dataset_version_id=version_id,
            source_type=DatasetLineageSourceType.INGESTION_JOB,
            source_id=job_id,
            relationship=DatasetLineageRelationship.INGESTED_FROM,
        )

        try:
            # Create the prerequisite records using the real PostgreSQL
            # database and its foreign-key constraints.
            async with sessions() as session:
                async with session.begin():
                    session.add(
                        ProviderORM(
                            provider_id=provider_id,
                            name="PostgreSQL integration test",
                            supported_asset_classes=[
                                AssetClass.FOREX.value
                            ],
                            supported_timeframes=[
                                Timeframe.M1.value
                            ],
                            supports_historical_data=True,
                            supports_quote_data=False,
                            is_enabled=True,
                        )
                    )

                    session.add(
                        DatasetORM(
                            dataset_id=dataset_id,
                            name=f"Integration dataset {token}",
                            data_kind=DatasetKind.OHLCV,
                            asset_class=AssetClass.FOREX,
                            instrument_symbol="EURUSD",
                            timeframe=Timeframe.M1,
                        )
                    )

                    session.add(
                        DatasetVersionORM(
                            dataset_version_id=version_id,
                            dataset_id=dataset_id,
                            version_number=1,
                            schema_version="1",
                            storage_format="parquet",
                            status=DatasetVersionStatus.STAGING,
                            record_count=0,
                        )
                    )

            job_repository = SQLAlchemyIngestionJobRepository(sessions)
            lineage_repository = (
                SQLAlchemyDatasetVersionLineageRepository(sessions)
            )

            await job_repository.add(job)

            stored_job = await job_repository.get_by_id(job_id)

            assert stored_job is not None
            assert stored_job.job_id == job_id
            assert stored_job.provider_id == provider_id
            assert stored_job.status == job.status

            await lineage_repository.add(lineage)

            stored_lineage = await lineage_repository.get_by_id(
                lineage_id
            )

            assert stored_lineage is not None
            assert stored_lineage.lineage_id == lineage_id
            assert stored_lineage.target_dataset_version_id == version_id
            assert stored_lineage.source_id == job_id
            assert (
                stored_lineage.relationship
                == DatasetLineageRelationship.INGESTED_FROM
            )

            by_target = (
                await lineage_repository.list_by_target_version_id(
                    version_id
                )
            )

            by_source = await lineage_repository.list_by_source(
                DatasetLineageSourceType.INGESTION_JOB,
                job_id,
            )

            assert [item.lineage_id for item in by_target] == [
                lineage_id
            ]
            assert [item.lineage_id for item in by_source] == [
                lineage_id
            ]

        finally:
            # Delete records in foreign-key-safe order.
            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        delete(DatasetVersionLineageORM).where(
                            DatasetVersionLineageORM.lineage_id
                            == lineage_id
                        )
                    )
                    await session.execute(
                        delete(IngestionJobDatasetVersionORM).where(
                            IngestionJobDatasetVersionORM.job_id
                            == job_id
                        )
                    )
                    await session.execute(
                        delete(IngestionJobORM).where(
                            IngestionJobORM.job_id == job_id
                        )
                    )
                    await session.execute(
                        delete(DatasetVersionORM).where(
                            DatasetVersionORM.dataset_version_id
                            == version_id
                        )
                    )
                    await session.execute(
                        delete(DatasetORM).where(
                            DatasetORM.dataset_id == dataset_id
                        )
                    )
                    await session.execute(
                        delete(ProviderORM).where(
                            ProviderORM.provider_id == provider_id
                        )
                    )

    try:
        asyncio.run(scenario(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    finally:
        asyncio.run(engine.dispose(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
