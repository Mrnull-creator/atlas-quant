"""Opt-in PostgreSQL integration tests for ingestion execution claims."""

import asyncio
import os
import selectors
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from atlas_quant.platform.config import get_settings
from atlas_quant.platform.database import (
    create_async_database_engine,
    create_async_session_factory,
)
from atlas_quant.services.market_data.domain.enums import AssetClass, Timeframe
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJob,
    IngestionJobStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_job import (
    IngestionJobDatasetVersionORM,
    IngestionJobORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_job_repository import (
    SQLAlchemyIngestionJobRepository,
)
from atlas_quant.services.market_data.infrastructure.persistence.instrument import (
    InstrumentORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider import (
    ProviderORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider_instrument_mapping import (
    ProviderInstrumentMappingORM,
)

pytestmark = pytest.mark.skipif(
    os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") != "1",
    reason=(
        "Set ATLAS_RUN_POSTGRES_INTEGRATION=1 "
        "to run PostgreSQL integration tests."
    ),
)


def test_ingestion_execution_claim_is_atomic_postgresql():
    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)
    sessions = create_async_session_factory(engine)

    async def scenario():
        token = uuid4().hex
        provider_id = f"claim-integration-{token}"
        instrument_id = uuid4()
        running_job_id = uuid4()
        pending_job_id = uuid4()
        missing_job_id = uuid4()
        symbol = f"EURUSD{token[:8].upper()}"

        now = datetime.now(timezone.utc)
        requested_start = now - timedelta(days=1)

        running_job = IngestionJob(
            job_id=running_job_id,
            instrument_id=instrument_id,
            provider_id=provider_id,
            asset_class=AssetClass.FOREX,
            instrument_symbol=symbol,
            timeframe=Timeframe.M1,
            requested_start=requested_start,
            requested_end=now,
            created_at=requested_start,
        )

        pending_job = IngestionJob(
            job_id=pending_job_id,
            instrument_id=instrument_id,
            provider_id=provider_id,
            asset_class=AssetClass.FOREX,
            instrument_symbol=symbol,
            timeframe=Timeframe.M1,
            requested_start=requested_start,
            requested_end=now,
            created_at=requested_start,
        )

        try:
            async with sessions() as session:
                async with session.begin():
                    session.add(
                        InstrumentORM(
                            instrument_id=instrument_id,
                            symbol=symbol,
                            asset_class=AssetClass.FOREX,
                        )
                    )
                    await session.flush()

                    session.add(
                        ProviderORM(
                            provider_id=provider_id,
                            name="Ingestion claim integration test",
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

            repository = SQLAlchemyIngestionJobRepository(sessions)

            await repository.add(running_job)
            await repository.add(pending_job)

            # Move one job into RUNNING before attempting its claim.
            running_job = replace(
                running_job,
                status=IngestionJobStatus.RUNNING,
                started_at=now,
            )
            await repository.update(running_job)

            # PENDING and nonexistent jobs must never be claimable.
            assert await repository.claim_execution(pending_job_id) is False
            assert await repository.claim_execution(missing_job_id) is False

            # Two independent repository instances represent two workers.
            worker_one = SQLAlchemyIngestionJobRepository(sessions)
            worker_two = SQLAlchemyIngestionJobRepository(sessions)

            results = await asyncio.gather(
                worker_one.claim_execution(running_job_id),
                worker_two.claim_execution(running_job_id),
            )

            # Exactly one worker must win the atomic database claim.
            assert sorted(results) == [False, True]

            # A later retry must also be rejected.
            assert (
                await repository.claim_execution(running_job_id)
                is False
            )

            async with sessions() as session:
                stored_job = await session.scalar(
                    select(IngestionJobORM).where(
                        IngestionJobORM.job_id == running_job_id
                    )
                )

                assert stored_job is not None
                assert stored_job.status == IngestionJobStatus.RUNNING
                assert stored_job.execution_claimed_at is not None

                pending_stored_job = await session.scalar(
                    select(IngestionJobORM).where(
                        IngestionJobORM.job_id == pending_job_id
                    )
                )

                assert pending_stored_job is not None
                assert pending_stored_job.execution_claimed_at is None

        finally:
            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        delete(IngestionJobDatasetVersionORM).where(
                            IngestionJobDatasetVersionORM.job_id.in_(
                                [running_job_id, pending_job_id]
                            )
                        )
                    )

                    await session.execute(
                        delete(IngestionJobORM).where(
                            IngestionJobORM.job_id.in_(
                                [running_job_id, pending_job_id]
                            )
                        )
                    )

                    await session.execute(
                        delete(InstrumentORM).where(
                            InstrumentORM.instrument_id == instrument_id
                        )
                    )

                    await session.execute(
                        delete(ProviderORM).where(
                            ProviderORM.provider_id == provider_id
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