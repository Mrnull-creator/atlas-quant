"""Tests for ingestion job ORM persistence mappings."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJobStatus,
)
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
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
from atlas_quant.services.market_data.infrastructure.persistence.instrument import (
    InstrumentORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider import (
    ProviderORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider_instrument_mapping import (
    ProviderInstrumentMappingORM,
)


@pytest.fixture
def engine():
    database_engine = create_engine("sqlite:///:memory:")

    @event.listens_for(database_engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    MarketDataBase.metadata.create_all(database_engine)

    yield database_engine

    database_engine.dispose()


@pytest.fixture
def references(engine):
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        provider = ProviderORM(
            provider_id="dukascopy",
            name="Dukascopy",
            supported_asset_classes=["forex"],
            supported_timeframes=["1m", "1h"],
            supports_historical_data=True,
            supports_quote_data=True,
            is_enabled=True,
        )

        instrument = InstrumentORM(
            symbol="EURUSD",
            asset_class=AssetClass.FOREX,
        )

        session.add_all([provider, instrument])
        session.flush()

        dataset = DatasetORM(
            name="EURUSD M1",
            data_kind="ohlcv",
            asset_class=AssetClass.FOREX,
            instrument_id=instrument.instrument_id,
            instrument_symbol="EURUSD",
            provider_id=None,
            timeframe=Timeframe.M1,
        )
        session.add(dataset)
        session.flush()

        version = DatasetVersionORM(
            dataset_id=dataset.dataset_id,
            version_number=1,
            schema_version="1",
            storage_format="parquet",
            status="staging",
            record_count=0,
            created_at=now,
        )
        session.add(version)
        session.commit()

        return (
            provider.provider_id,
            instrument.instrument_id,
            version.dataset_version_id,
        )


def make_job(provider_id, **overrides):
    now = datetime.now(timezone.utc)

    values = {
        "provider_id": provider_id,
        "asset_class": AssetClass.FOREX,
        "instrument_symbol": "EURUSD",
        "timeframe": Timeframe.M1,
        "requested_start": now - timedelta(days=1),
        "requested_end": now,
        "status": IngestionJobStatus.PENDING,
        "records_downloaded": 0,
        "records_written": 0,
        "records_rejected": 0,
        "created_at": now,
    }
    values.update(overrides)

    return IngestionJobORM(**values)


def test_tables_are_registered():
    assert "ingestion_jobs" in MarketDataBase.metadata.tables
    assert (
        "ingestion_job_dataset_versions"
        in MarketDataBase.metadata.tables
    )


def test_job_primary_key_is_uuid():
    column = IngestionJobORM.__table__.c.job_id

    assert column.primary_key is True
    assert column.type.python_type is UUID


def test_job_provider_foreign_key_is_restrictive():
    foreign_key = next(
        foreign_key
        for foreign_key in IngestionJobORM.__table__.foreign_keys
        if foreign_key.parent.name == "provider_id"
    )

    assert foreign_key.target_fullname == (
        "market_data_providers.provider_id"
    )
    assert foreign_key.ondelete == "RESTRICT"


def test_job_instrument_foreign_key_is_restrictive():
    foreign_key = next(
        foreign_key
        for foreign_key in IngestionJobORM.__table__.foreign_keys
        if foreign_key.parent.name == "instrument_id"
    )

    assert foreign_key.target_fullname == "instruments.instrument_id"
    assert foreign_key.ondelete == "RESTRICT"


def test_output_association_has_composite_primary_key():
    table = IngestionJobDatasetVersionORM.__table__

    assert set(table.primary_key.columns.keys()) == {
        "job_id",
        "dataset_version_id",
    }


def test_output_association_foreign_keys_are_restrictive():
    foreign_keys = {
        foreign_key.parent.name: foreign_key
        for foreign_key in (
            IngestionJobDatasetVersionORM.__table__.foreign_keys
        )
    }

    assert set(foreign_keys) == {"job_id", "dataset_version_id"}
    assert all(
        foreign_key.ondelete == "RESTRICT"
        for foreign_key in foreign_keys.values()
    )


def test_job_and_output_version_can_be_persisted(
    engine,
    references,
):
    provider_id, instrument_id, version_id = references

    with Session(engine) as session:
        job = make_job(
            provider_id,
            instrument_id=instrument_id,
        )
        session.add(job)
        session.flush()

        association = IngestionJobDatasetVersionORM(
            job_id=job.job_id,
            dataset_version_id=version_id,
        )
        session.add(association)
        session.commit()

        persisted = session.get(
            IngestionJobORM,
            job.job_id,
        )

        assert persisted is not None
        assert persisted.status == IngestionJobStatus.PENDING
        assert isinstance(persisted.job_id, UUID)
        assert persisted.instrument_id == instrument_id

        persisted_association = session.get(
            IngestionJobDatasetVersionORM,
            (job.job_id, version_id),
        )

        assert persisted_association is not None


def test_job_rejects_unknown_provider(engine):
    with Session(engine) as session:
        session.add(make_job("unknown-provider"))

        with pytest.raises(IntegrityError):
            session.commit()


def test_job_rejects_unknown_instrument(engine, references):
    provider_id, _, _ = references

    with Session(engine) as session:
        session.add(
            make_job(provider_id, instrument_id=uuid4())
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_output_association_rejects_unknown_job(
    engine,
    references,
):
    _, _, version_id = references

    with Session(engine) as session:
        session.add(
            IngestionJobDatasetVersionORM(
                job_id=uuid4(),
                dataset_version_id=version_id,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_output_association_rejects_unknown_dataset_version(
    engine,
    references,
):
    provider_id, _, _ = references

    with Session(engine) as session:
        job = make_job(provider_id)
        session.add(job)
        session.flush()

        session.add(
            IngestionJobDatasetVersionORM(
                job_id=job.job_id,
                dataset_version_id=uuid4(),
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_pending_job_rejects_start_timestamp(
    engine,
    references,
):
    provider_id, _, _ = references
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        session.add(
            make_job(
                provider_id,
                started_at=now,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_running_job_requires_start_timestamp(
    engine,
    references,
):
    provider_id, _, _ = references

    with Session(engine) as session:
        session.add(
            make_job(
                provider_id,
                status=IngestionJobStatus.RUNNING,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_failed_job_requires_failure_message(
    engine,
    references,
):
    provider_id, _, _ = references
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        session.add(
            make_job(
                provider_id,
                status=IngestionJobStatus.FAILED,
                completed_at=now,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_negative_record_count_is_rejected(
    engine,
    references,
):
    provider_id, _, _ = references

    with Session(engine) as session:
        session.add(
            make_job(
                provider_id,
                records_downloaded=-1,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_invalid_requested_range_is_rejected(
    engine,
    references,
):
    provider_id, _, _ = references
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        session.add(
            make_job(
                provider_id,
                requested_start=now,
                requested_end=now - timedelta(hours=1),
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()
