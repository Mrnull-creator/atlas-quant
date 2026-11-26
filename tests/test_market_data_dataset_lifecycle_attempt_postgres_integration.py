
"""Opt-in PostgreSQL tests for attempt-bound dataset staging."""

import asyncio
import os
import selectors
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from atlas_quant.platform.config import get_settings
from atlas_quant.platform.database import (
    create_async_database_engine,
    create_async_session_factory,
)
from atlas_quant.services.market_data.application.repository_errors import (
    IngestionJobRepositoryExecutionOwnershipError,
)
from atlas_quant.services.market_data.domain.datasets import (
    DatasetKind,
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJob,
    IngestionJobStatus,
)
from atlas_quant.services.market_data.domain.ingestion_attempt import (
    IngestionAttemptStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_lifecycle_repository import (
    SQLAlchemyDatasetLifecycleRepository,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version import (
    DatasetVersionORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_attempt import (
    IngestionAttemptORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_job import (
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


def test_attempt_bound_staging_rejects_stale_owners_postgresql():
    """Verify attempt ownership, mapping, and dataset identity fencing."""

    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)
    sessions = create_async_session_factory(engine)

    async def scenario():
        token = uuid4().hex
        provider_id = f"staging-integration-{token}"
        instrument_id = uuid4()
        mapping_id = uuid4()
        job_id = uuid4()
        dataset_id = uuid4()
        mismatched_dataset_id = uuid4()
        symbol = f"EURUSD{token[:8].upper()}"

        now = datetime.now(timezone.utc)
        requested_start = now - timedelta(days=1)

        job = IngestionJob(
            job_id=job_id,
            instrument_id=instrument_id,
            instrument_symbol=symbol,
            provider_id=provider_id,
            provider_mapping_id=mapping_id,
            asset_class=AssetClass.FOREX,
            data_kind=DatasetKind.OHLCV,
            timeframe=Timeframe.M1,
            requested_start=requested_start,
            requested_end=now,
            created_at=requested_start,
        )

        job_repository = SQLAlchemyIngestionJobRepository(sessions)
        lifecycle_repository = SQLAlchemyDatasetLifecycleRepository(
            sessions
        )

        try:
            # Create the provider, instrument, mapping, and datasets.
            async with sessions() as session:
                async with session.begin():
                    session.add(
                        InstrumentORM(
                            instrument_id=instrument_id,
                            symbol=symbol,
                            asset_class=AssetClass.FOREX,
                        )
                    )
                    session.add(
                        ProviderORM(
                            provider_id=provider_id,
                            name="Dataset lifecycle integration test",
                            supported_asset_classes=[
                                AssetClass.FOREX.value
                            ],
                            supported_timeframes=[
                                Timeframe.M1.value,
                                Timeframe.M5.value,
                            ],
                            supports_historical_data=True,
                            supports_quote_data=False,
                            is_enabled=True,
                        )
                    )
                    await session.flush()

                    session.add(
                        ProviderInstrumentMappingORM(
                            mapping_id=mapping_id,
                            instrument_id=instrument_id,
                            provider_id=provider_id,
                            provider_symbol=symbol,
                            provider_exchange="",
                            is_active=True,
                        )
                    )

                    session.add_all(
                        [
                            DatasetORM(
                                dataset_id=dataset_id,
                                name=f"{symbol} M1 integration dataset",
                                data_kind=DatasetKind.OHLCV,
                                asset_class=AssetClass.FOREX,
                                instrument_id=instrument_id,
                                instrument_symbol=symbol,
                                timeframe=Timeframe.M1,
                            ),
                            DatasetORM(
                                dataset_id=mismatched_dataset_id,
                                name=f"{symbol} M5 integration dataset",
                                data_kind=DatasetKind.OHLCV,
                                asset_class=AssetClass.FOREX,
                                instrument_id=instrument_id,
                                instrument_symbol=symbol,
                                timeframe=Timeframe.M5,
                            ),
                        ]
                    )

            # Create and start the ingestion job.
            await job_repository.add(job)

            running_job = replace(
                job,
                status=IngestionJobStatus.RUNNING,
                started_at=now,
            )
            await job_repository.update(running_job)

            attempt = await job_repository.claim_attempt(
                job_id,
                "staging-integration-worker",
                lease_duration=timedelta(minutes=5),
            )
            assert attempt is not None
            assert attempt.status == IngestionAttemptStatus.RUNNING

            # A valid owner can create a staging version.
            version = (
                await lifecycle_repository.create_next_staging_version(
                    dataset_id,
                    schema_version="1",
                    storage_format="parquet",
                    attempt=attempt,
                )
            )

            assert version.dataset_id == dataset_id
            assert version.version_number == 1
            assert version.ingestion_attempt_id == attempt.attempt_id

            async with sessions() as session:
                persisted_version = await session.scalar(
                    select(DatasetVersionORM).where(
                        DatasetVersionORM.dataset_version_id
                        == version.dataset_version_id
                    )
                )

                assert persisted_version is not None
                assert (
                    persisted_version.ingestion_attempt_id
                    == attempt.attempt_id
                )

            # A valid attempt can publish its staging version.
            publication_metadata = StorageObjectMetadata(
                storage_uri=f"file:///tmp/{token}/version-1.parquet",
                storage_format="parquet",
                checksum_algorithm="sha256",
                checksum="a" * 64,
                size_bytes=128,
                record_count=1,
                first_timestamp=requested_start,
                last_timestamp=now,
            )

            published_version = (
                await lifecycle_repository.publish_staging_version(
                    version.dataset_version_id,
                    publication_metadata,
                    attempt=attempt,
                )
            )
            assert published_version.status == DatasetVersionStatus.AVAILABLE

            async with sessions() as session:
                persisted_dataset = await session.scalar(
                    select(DatasetORM).where(
                        DatasetORM.dataset_id == dataset_id
                    )
                )
                assert persisted_dataset is not None
                assert (
                    persisted_dataset.latest_version_id
                    == version.dataset_version_id
                )

            # A different worker identity cannot stage a version.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await lifecycle_repository.create_next_staging_version(
                    dataset_id,
                    schema_version="1",
                    storage_format="parquet",
                    attempt=replace(
                        attempt,
                        worker_id="unauthorized-staging-worker",
                    ),
                )

            # An incorrect fencing token cannot stage a version.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await lifecycle_repository.create_next_staging_version(
                    dataset_id,
                    schema_version="1",
                    storage_format="parquet",
                    attempt=replace(
                        attempt,
                        fencing_token=attempt.fencing_token + 1,
                    ),
                )

            # A dataset with a different timeframe cannot be used.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await lifecycle_repository.create_next_staging_version(
                    mismatched_dataset_id,
                    schema_version="1",
                    storage_format="parquet",
                    attempt=attempt,
                )

            # An inactive provider mapping cannot authorize staging.
            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        update(ProviderInstrumentMappingORM)
                        .where(
                            ProviderInstrumentMappingORM.mapping_id
                            == mapping_id
                        )
                        .values(is_active=False)
                    )

            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await lifecycle_repository.create_next_staging_version(
                    dataset_id,
                    schema_version="1",
                    storage_format="parquet",
                    attempt=attempt,
                )

            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        update(ProviderInstrumentMappingORM)
                        .where(
                            ProviderInstrumentMappingORM.mapping_id
                            == mapping_id
                        )
                        .values(is_active=True)
                    )

            # Expire the persisted lease while respecting timestamp checks.
            expired_started_at = datetime.now(timezone.utc) - timedelta(
                minutes=3
            )
            expired_heartbeat = datetime.now(timezone.utc) - timedelta(
                minutes=2
            )
            expired_lease = datetime.now(timezone.utc) - timedelta(
                minutes=1
            )

            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        update(IngestionAttemptORM)
                        .where(
                            IngestionAttemptORM.attempt_id
                            == attempt.attempt_id
                        )
                        .values(
                            started_at=expired_started_at,
                            heartbeat_at=expired_heartbeat,
                            lease_expires_at=expired_lease,
                        )
                    )

            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await lifecycle_repository.create_next_staging_version(
                    dataset_id,
                    schema_version="1",
                    storage_format="parquet",
                    attempt=attempt,
                )

            # Recover the job with a new attempt. The former attempt must
            # remain fenced even though its caller still has its old token.
            replacement_attempt = await job_repository.claim_attempt(
                job_id,
                "staging-recovery-worker",
                lease_duration=timedelta(minutes=5),
            )

            assert replacement_attempt is not None
            assert (
                replacement_attempt.attempt_number
                == attempt.attempt_number + 1
            )
            assert (
                replacement_attempt.fencing_token
                > attempt.fencing_token
            )

            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await lifecycle_repository.create_next_staging_version(
                    dataset_id,
                    schema_version="1",
                    storage_format="parquet",
                    attempt=attempt,
                )

            # The replacement owner can still stage successfully.
            replacement_version = (
                await lifecycle_repository.create_next_staging_version(
                    dataset_id,
                    schema_version="1",
                    storage_format="parquet",
                    attempt=replacement_attempt,
                )
            )

            assert replacement_version.version_number == 2
            assert (
                replacement_version.ingestion_attempt_id
                == replacement_attempt.attempt_id
            )

            # Create a third staging version under the replacement owner.
            third_version = (
                await lifecycle_repository.create_next_staging_version(
                    dataset_id,
                    schema_version="1",
                    storage_format="parquet",
                    attempt=replacement_attempt,
                )
            )
            assert third_version.version_number == 3

            replacement_metadata = replace(
                publication_metadata,
                storage_uri=f"file:///tmp/{token}/version-2.parquet",
            )

            # The stale owner cannot fail the replacement's staging version.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await lifecycle_repository.fail_staging_version(
                    third_version.dataset_version_id,
                    attempt=attempt,
                )

            # The stale owner cannot publish the replacement's version.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await lifecycle_repository.publish_staging_version(
                    replacement_version.dataset_version_id,
                    replacement_metadata,
                    attempt=attempt,
                )

            # Rejected operations must leave state and latest pointer intact.
            async with sessions() as session:
                persisted_dataset = await session.scalar(
                    select(DatasetORM).where(
                        DatasetORM.dataset_id == dataset_id
                    )
                )
                persisted_second = await session.scalar(
                    select(DatasetVersionORM).where(
                        DatasetVersionORM.dataset_version_id
                        == replacement_version.dataset_version_id
                    )
                )
                persisted_third = await session.scalar(
                    select(DatasetVersionORM).where(
                        DatasetVersionORM.dataset_version_id
                        == third_version.dataset_version_id
                    )
                )

                assert persisted_dataset is not None
                assert persisted_second is not None
                assert persisted_third is not None
                assert (
                    persisted_dataset.latest_version_id
                    == version.dataset_version_id
                )
                assert persisted_second.status == DatasetVersionStatus.STAGING
                assert persisted_third.status == DatasetVersionStatus.STAGING

            # The active replacement owner can publish its own version.
            second_published = (
                await lifecycle_repository.publish_staging_version(
                    replacement_version.dataset_version_id,
                    replacement_metadata,
                    attempt=replacement_attempt,
                )
            )
            assert second_published.status == DatasetVersionStatus.AVAILABLE

            # The active replacement owner can fail its own staging version.
            third_failed = await lifecycle_repository.fail_staging_version(
                third_version.dataset_version_id,
                attempt=replacement_attempt,
            )
            assert third_failed.status == DatasetVersionStatus.FAILED

            async with sessions() as session:
                persisted_dataset = await session.scalar(
                    select(DatasetORM).where(
                        DatasetORM.dataset_id == dataset_id
                    )
                )
                assert persisted_dataset is not None
                assert (
                    persisted_dataset.latest_version_id
                    == replacement_version.dataset_version_id
                )

        finally:
            async with sessions() as session:
                async with session.begin():
                    # Break the composite current-attempt reference first.
                    await session.execute(
                        update(IngestionJobORM)
                        .where(IngestionJobORM.job_id == job_id)
                        .values(current_attempt_id=None)
                    )

                    # Clear the latest-version FK before deleting versions.
                    await session.execute(
                        update(DatasetORM)
                        .where(
                            DatasetORM.dataset_id.in_(
                                [dataset_id, mismatched_dataset_id]
                            )
                        )
                        .values(latest_version_id=None)
                    )

                    await session.execute(
                        delete(DatasetVersionORM).where(
                            DatasetVersionORM.dataset_id.in_(
                                [dataset_id, mismatched_dataset_id]
                            )
                        )
                    )

                    await session.execute(
                        delete(DatasetORM).where(
                            DatasetORM.dataset_id.in_(
                                [dataset_id, mismatched_dataset_id]
                            )
                        )
                    )

                    await session.execute(
                        delete(IngestionAttemptORM).where(
                            IngestionAttemptORM.job_id == job_id
                        )
                    )

                    await session.execute(
                        delete(IngestionJobORM).where(
                            IngestionJobORM.job_id == job_id
                        )
                    )

                    await session.execute(
                        delete(ProviderInstrumentMappingORM).where(
                            ProviderInstrumentMappingORM.mapping_id
                            == mapping_id
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
