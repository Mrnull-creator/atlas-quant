"""Opt-in PostgreSQL integration tests for ingestion attempt ownership."""

import asyncio
import os
import selectors
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update

from atlas_quant.platform.config import get_settings
from atlas_quant.platform.database import (
    create_async_database_engine,
    create_async_session_factory,
)
from atlas_quant.services.market_data.application.repository_errors import (
    IngestionJobRepositoryExecutionOwnershipError,
    IngestionJobRepositoryIntegrityError,
)
from atlas_quant.services.market_data.domain.datasets import (
    DatasetKind,
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.domain.enums import AssetClass, Timeframe
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJob,
    IngestionJobStatus,
)
from atlas_quant.services.market_data.domain.ingestion_attempt import (
    IngestionAttemptStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_attempt import (
    IngestionAttemptORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version import (
    DatasetVersionORM,
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


def test_ingestion_attempt_ownership_and_lease_fencing_postgresql():
    """Verify concurrent claims, lease recovery, and stale-owner rejection."""

    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)
    sessions = create_async_session_factory(engine)

    async def scenario():
        token = uuid4().hex
        provider_id = f"attempt-integration-{token}"
        instrument_id = uuid4()
        job_id = uuid4()
        symbol = f"EURUSD{token[:8].upper()}"

        now = datetime.now(timezone.utc)
        requested_start = now - timedelta(days=1)

        job = IngestionJob(
            job_id=job_id,
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
                            name="Ingestion attempt integration test",
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
            await repository.add(job)

            running_job = replace(
                job,
                status=IngestionJobStatus.RUNNING,
                started_at=now,
            )
            await repository.update(running_job)

            worker_one = SQLAlchemyIngestionJobRepository(sessions)
            worker_two = SQLAlchemyIngestionJobRepository(sessions)

            # Two independent workers compete for the same job.
            results = await asyncio.gather(
                worker_one.claim_attempt(
                    job_id,
                    "integration-worker-one",
                    lease_duration=timedelta(seconds=30),
                ),
                worker_two.claim_attempt(
                    job_id,
                    "integration-worker-two",
                    lease_duration=timedelta(seconds=30),
                ),
            )

            # Exactly one worker must acquire the first attempt.
            acquired = [attempt for attempt in results if attempt is not None]
            rejected = [attempt for attempt in results if attempt is None]

            assert len(acquired) == 1
            assert len(rejected) == 1

            first_attempt = acquired[0]
            assert first_attempt.attempt_number == 1
            assert first_attempt.fencing_token == 1
            assert first_attempt.status == IngestionAttemptStatus.RUNNING

            winning_repository = (
                worker_one
                if first_attempt.worker_id == "integration-worker-one"
                else worker_two
            )
            losing_repository = (
                worker_two
                if first_attempt.worker_id == "integration-worker-one"
                else worker_one
            )
            losing_worker_id = (
                "integration-worker-two"
                if first_attempt.worker_id == "integration-worker-one"
                else "integration-worker-one"
            )

            # An active lease prevents another attempt from being acquired.
            assert (
                await losing_repository.claim_attempt(
                    job_id,
                    losing_worker_id,
                    lease_duration=timedelta(seconds=30),
                )
                is None
            )

            # The current owner can renew its lease.
            renewed_first = await winning_repository.renew_attempt_lease(
                job_id,
                first_attempt.attempt_id,
                first_attempt.fencing_token,
                first_attempt.worker_id,
                lease_duration=timedelta(seconds=30),
            )

            assert renewed_first.status == IngestionAttemptStatus.RUNNING
            assert renewed_first.lease_expires_at > renewed_first.heartbeat_at

            # Simulate lease expiry using the PostgreSQL clock.
            # Both timestamps are moved together to preserve the
            # database constraint lease_expires_at > heartbeat_at.
            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        text(
    """
    UPDATE ingestion_attempts
    SET
        heartbeat_at = started_at,
        lease_expires_at = started_at + INTERVAL '1 microsecond'
    WHERE attempt_id = :attempt_id
    """
),
                        {"attempt_id": first_attempt.attempt_id},
                    )

            # The other worker can now acquire a new attempt.
            second_attempt = await losing_repository.claim_attempt(
                job_id,
                losing_worker_id,
                lease_duration=timedelta(seconds=30),
            )

            assert second_attempt is not None
            assert second_attempt.attempt_number == 2
            assert second_attempt.fencing_token == 2
            assert second_attempt.worker_id == losing_worker_id
            assert second_attempt.status == IngestionAttemptStatus.RUNNING

            # The previous attempt must be marked as expired.
            async with sessions() as session:
                old_attempt = await session.scalar(
                    select(IngestionAttemptORM).where(
                        IngestionAttemptORM.attempt_id
                        == first_attempt.attempt_id
                    )
                )

                assert old_attempt is not None
                assert (
                    old_attempt.status
                    == IngestionAttemptStatus.LEASE_EXPIRED
                )
                assert old_attempt.finished_at is not None

            # The former owner must not renew its expired attempt,
            # even though it still has the old attempt ID and token.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await winning_repository.renew_attempt_lease(
                    job_id,
                    first_attempt.attempt_id,
                    first_attempt.fencing_token,
                    first_attempt.worker_id,
                    lease_duration=timedelta(seconds=30),
                )

            # The new owner can renew the authoritative attempt.
            renewed_second = await losing_repository.renew_attempt_lease(
                job_id,
                second_attempt.attempt_id,
                second_attempt.fencing_token,
                second_attempt.worker_id,
                lease_duration=timedelta(seconds=30),
            )

            assert renewed_second.status == IngestionAttemptStatus.RUNNING
            assert renewed_second.lease_expires_at > (
                renewed_second.heartbeat_at
            )

            # A superseded attempt cannot update the job, even with its
            # original worker identity and fencing token.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await winning_repository.update_owned(
                    running_job,
                    first_attempt.attempt_id,
                    first_attempt.fencing_token,
                    first_attempt.worker_id,
                )

            # The current attempt rejects an incorrect fencing token.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await losing_repository.update_owned(
                    running_job,
                    second_attempt.attempt_id,
                    second_attempt.fencing_token + 1,
                    second_attempt.worker_id,
                )

            # The current attempt also rejects a different worker identity.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await losing_repository.update_owned(
                    running_job,
                    second_attempt.attempt_id,
                    second_attempt.fencing_token,
                    "unauthorized-worker",
                )

            # Once an attempt is associated with a job, ordinary updates
            # cannot bypass the ownership check.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await losing_repository.update(running_job)

            # The authoritative worker can update progress.
            progressed_job = replace(
                running_job,
                records_downloaded=10,
                records_written=8,
                records_rejected=2,
            )
            await losing_repository.update_owned(
                progressed_job,
                second_attempt.attempt_id,
                second_attempt.fencing_token,
                second_attempt.worker_id,
            )

            persisted_progress = await losing_repository.get_by_id(job_id)
            assert persisted_progress is not None
            assert persisted_progress.status == IngestionJobStatus.RUNNING
            assert persisted_progress.records_downloaded == 10
            assert persisted_progress.records_written == 8
            assert persisted_progress.records_rejected == 2

            # A failed dataset-version association must roll back both
            # the job transition and the attempt terminal transition.
            invalid_terminal_job = replace(
                progressed_job,
                status=IngestionJobStatus.SUCCEEDED,
                completed_at=datetime.now(timezone.utc),
                produced_dataset_version_ids=(uuid4(),),
            )

            with pytest.raises(IngestionJobRepositoryIntegrityError):
                await losing_repository.update_owned(
                    invalid_terminal_job,
                    second_attempt.attempt_id,
                    second_attempt.fencing_token,
                    second_attempt.worker_id,
                )

            # The failed transaction must leave the job unchanged.
            still_running_job = await losing_repository.get_by_id(job_id)
            assert still_running_job is not None
            assert still_running_job.status == IngestionJobStatus.RUNNING
            assert still_running_job.completed_at is None
            assert still_running_job.produced_dataset_version_ids == ()

            # The attempt must also remain authoritative and running.
            async with sessions() as session:
                still_running_attempt = await session.scalar(
                    select(IngestionAttemptORM).where(
                        IngestionAttemptORM.attempt_id
                        == second_attempt.attempt_id
                    )
                )

                assert still_running_attempt is not None
                assert (
                    still_running_attempt.status
                    == IngestionAttemptStatus.RUNNING
                )
                assert still_running_attempt.finished_at is None

            # Job success and attempt success must be committed together.
            completed_job = replace(
                progressed_job,
                status=IngestionJobStatus.SUCCEEDED,
                completed_at=datetime.now(timezone.utc),
            )
            await losing_repository.update_owned(
                completed_job,
                second_attempt.attempt_id,
                second_attempt.fencing_token,
                second_attempt.worker_id,
            )

            persisted_job = await losing_repository.get_by_id(job_id)
            assert persisted_job is not None
            assert persisted_job.status == IngestionJobStatus.SUCCEEDED
            assert persisted_job.completed_at is not None

            async with sessions() as session:
                completed_attempt = await session.scalar(
                    select(IngestionAttemptORM).where(
                        IngestionAttemptORM.attempt_id
                        == second_attempt.attempt_id
                    )
                )

                assert completed_attempt is not None
                assert (
                    completed_attempt.status
                    == IngestionAttemptStatus.SUCCEEDED
                )
                assert completed_attempt.finished_at is not None
                assert completed_attempt.failure_code is None
                assert completed_attempt.failure_message is None

        finally:
            async with sessions() as session:
                async with session.begin():
                    # Remove the composite current-attempt reference first.
                    await session.execute(
                        update(IngestionJobORM)
                        .where(IngestionJobORM.job_id == job_id)
                        .values(current_attempt_id=None)
                    )

                    # Attempts reference their owning job.
                    await session.execute(
                        delete(IngestionAttemptORM).where(
                            IngestionAttemptORM.job_id == job_id
                        )
                    )

                    await session.execute(
                        delete(IngestionJobDatasetVersionORM).where(
                            IngestionJobDatasetVersionORM.job_id == job_id
                        )
                    )

                    await session.execute(
                        delete(IngestionJobORM).where(
                            IngestionJobORM.job_id == job_id
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

@pytest.mark.parametrize(
    (
        "job_status",
        "attempt_status",
        "failure_code",
        "failure_message",
    ),
    [
        (
            IngestionJobStatus.SUCCEEDED,
            IngestionAttemptStatus.SUCCEEDED,
            None,
            None,
        ),
        (
            IngestionJobStatus.PARTIALLY_SUCCEEDED,
            IngestionAttemptStatus.PARTIALLY_SUCCEEDED,
            "PARTIAL_DATA",
            "Some requested market data could not be ingested.",
        ),
        (
            IngestionJobStatus.FAILED,
            IngestionAttemptStatus.FAILED,
            "PROVIDER_ERROR",
            "The provider rejected the ingestion request.",
        ),
        (
            IngestionJobStatus.CANCELLED,
            IngestionAttemptStatus.CANCELLED,
            None,
            None,
        ),
    ],
)
def test_ingestion_terminal_outcomes_are_fenced_postgresql(
    job_status,
    attempt_status,
    failure_code,
    failure_message,
):
    """Verify atomic job/attempt terminal mappings and terminal immutability."""

    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)
    sessions = create_async_session_factory(engine)

    async def scenario():
        token = uuid4().hex
        provider_id = f"terminal-test-{token}"
        instrument_id = uuid4()
        job_id = uuid4()
        symbol = f"EURUSD{token[:8].upper()}"

        now = datetime.now(timezone.utc)
        requested_start = now - timedelta(days=1)
        completed_at = now + timedelta(seconds=1)

        job = IngestionJob(
            job_id=job_id,
            instrument_id=instrument_id,
            provider_id=provider_id,
            asset_class=AssetClass.FOREX,
            instrument_symbol=symbol,
            timeframe=Timeframe.M1,
            requested_start=requested_start,
            requested_end=now,
            created_at=requested_start,
        )

        dataset_id = uuid4()
        dataset_version_id = uuid4()
        dataset_created = False

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
                            name="Ingestion terminal-state integration test",
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
            await repository.add(job)

            running_job = replace(
                job,
                status=IngestionJobStatus.RUNNING,
                started_at=now,
            )
            await repository.update(running_job)

            attempt = await repository.claim_attempt(
                job_id,
                f"terminal-worker-{token}",
                lease_duration=timedelta(minutes=5),
            )

            assert attempt is not None
            assert attempt.status == IngestionAttemptStatus.RUNNING

            produced_version_ids = ()

            if job_status == IngestionJobStatus.PARTIALLY_SUCCEEDED:
                # Partial success must reference a real, verified version
                # owned by this dataset and this ingestion attempt.
                async with sessions() as session:
                    async with session.begin():
                        session.add(
                            DatasetORM(
                                dataset_id=dataset_id,
                                name=f"Terminal test dataset {token}",
                                data_kind=DatasetKind.OHLCV,
                                asset_class=AssetClass.FOREX,
                                instrument_id=instrument_id,
                                instrument_symbol=symbol,
                                provider_id=provider_id,
                                timeframe=Timeframe.M1,
                            )
                        )
                        await session.flush()

                        session.add(
                            DatasetVersionORM(
                                dataset_version_id=dataset_version_id,
                                dataset_id=dataset_id,
                                version_number=1,
                                schema_version="1",
                                storage_format="parquet",
                                status=DatasetVersionStatus.VERIFIED,
                                first_timestamp=now - timedelta(minutes=1),
                                last_timestamp=now,
                                record_count=1,
                                storage_uri=(
                                    f"file:///atlas-test/{token}/data.parquet"
                                ),
                                checksum_algorithm="sha256",
                                checksum="a" * 64,
                                created_at=now,
                                verified_at=now,
                                published_at=None,
                                ingestion_attempt_id=attempt.attempt_id,
                            )
                        )

                dataset_created = True
                produced_version_ids = (dataset_version_id,)

            terminal_job = replace(
                running_job,
                status=job_status,
                completed_at=completed_at,
                failure_code=failure_code,
                failure_message=failure_message,
                produced_dataset_version_ids=produced_version_ids,
            )

            await repository.update_owned(
                terminal_job,
                attempt.attempt_id,
                attempt.fencing_token,
                attempt.worker_id,
            )

            persisted_job = await repository.get_by_id(job_id)
            assert persisted_job is not None
            assert persisted_job.status == job_status
            assert persisted_job.completed_at is not None
            assert persisted_job.failure_code == failure_code
            assert persisted_job.failure_message == failure_message
            assert (
                persisted_job.produced_dataset_version_ids
                == produced_version_ids
            )

            async with sessions() as session:
                persisted_attempt = await session.scalar(
                    select(IngestionAttemptORM).where(
                        IngestionAttemptORM.attempt_id
                        == attempt.attempt_id
                    )
                )

                assert persisted_attempt is not None
                assert persisted_attempt.status == attempt_status
                assert persisted_attempt.finished_at is not None
                assert persisted_attempt.failure_code == failure_code
                assert (
                    persisted_attempt.failure_message == failure_message
                )

            # A terminal job cannot be updated again, even by the
            # worker that completed its authoritative attempt.
            with pytest.raises(
                IngestionJobRepositoryExecutionOwnershipError
            ):
                await repository.update_owned(
                    terminal_job,
                    attempt.attempt_id,
                    attempt.fencing_token,
                    attempt.worker_id,
                )

        finally:
            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        update(IngestionJobORM)
                        .where(IngestionJobORM.job_id == job_id)
                        .values(current_attempt_id=None)
                    )

                    await session.execute(
                        delete(IngestionJobDatasetVersionORM).where(
                            IngestionJobDatasetVersionORM.job_id == job_id
                        )
                    )

                    if dataset_created:
                        await session.execute(
                            delete(DatasetVersionORM).where(
                                DatasetVersionORM.dataset_version_id
                                == dataset_version_id
                            )
                        )
                        await session.execute(
                            delete(DatasetORM).where(
                                DatasetORM.dataset_id == dataset_id
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
