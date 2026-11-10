"""Tests for dataset version lineage ORM persistence mappings."""

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
from atlas_quant.services.market_data.domain.dataset_lineage import (
    DatasetLineageRelationship,
    DatasetLineageSourceType,
)
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)
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
    IngestionJobORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.instrument import (
    InstrumentORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider import (
    ProviderORM,
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

        datasets = [
            DatasetORM(
                name="EURUSD source",
                data_kind="ohlcv",
                asset_class=AssetClass.FOREX,
                instrument_id=instrument.instrument_id,
                instrument_symbol="EURUSD",
                provider_id=None,
                timeframe=Timeframe.M1,
            ),
            DatasetORM(
                name="EURUSD target",
                data_kind="ohlcv",
                asset_class=AssetClass.FOREX,
                instrument_id=instrument.instrument_id,
                instrument_symbol="EURUSD",
                provider_id=None,
                timeframe=Timeframe.H1,
            ),
        ]

        session.add_all(datasets)
        session.flush()

        versions = [
            DatasetVersionORM(
                dataset_id=dataset.dataset_id,
                version_number=1,
                schema_version="1",
                storage_format="parquet",
                status="staging",
                record_count=0,
                created_at=now,
            )
            for dataset in datasets
        ]

        session.add_all(versions)
        session.flush()

        job = make_job(provider.provider_id)
        session.add(job)
        session.commit()

        return {
            "provider_id": provider.provider_id,
            "job_id": job.job_id,
            "source_version_id": versions[0].dataset_version_id,
            "target_version_id": versions[1].dataset_version_id,
        }


def make_job(provider_id):
    now = datetime.now(timezone.utc)

    return IngestionJobORM(
        provider_id=provider_id,
        asset_class=AssetClass.FOREX,
        instrument_symbol="EURUSD",
        timeframe=Timeframe.M1,
        requested_start=now - timedelta(days=1),
        requested_end=now,
        status=IngestionJobStatus.PENDING,
        records_downloaded=0,
        records_written=0,
        records_rejected=0,
        created_at=now,
    )


def make_lineage(
    target_version_id,
    *,
    source_type=DatasetLineageSourceType.INGESTION_JOB,
    job_id=None,
    source_version_id=None,
    relationship=DatasetLineageRelationship.INGESTED_FROM,
):
    if job_id is None and source_type == DatasetLineageSourceType.INGESTION_JOB:
        job_id = uuid4()

    if (
        source_version_id is None
        and source_type == DatasetLineageSourceType.DATASET_VERSION
    ):
        source_version_id = uuid4()

    return DatasetVersionLineageORM(
        lineage_id=uuid4(),
        target_dataset_version_id=target_version_id,
        source_type=source_type,
        ingestion_job_id=job_id,
        source_dataset_version_id=source_version_id,
        relationship_type=relationship,
    )


def test_lineage_table_is_registered():
    assert "dataset_version_lineage" in MarketDataBase.metadata.tables


def test_lineage_primary_key_is_uuid():
    column = DatasetVersionLineageORM.__table__.c.lineage_id

    assert column.primary_key is True
    assert column.type.python_type is UUID


def test_lineage_foreign_keys_are_restrictive():
    foreign_keys = {
        foreign_key.parent.name: foreign_key
        for foreign_key in DatasetVersionLineageORM.__table__.foreign_keys
    }

    assert set(foreign_keys) == {
        "target_dataset_version_id",
        "ingestion_job_id",
        "source_dataset_version_id",
    }

    assert all(
        foreign_key.ondelete == "RESTRICT"
        for foreign_key in foreign_keys.values()
    )


def test_ingestion_job_lineage_can_be_persisted(engine, references):
    with Session(engine) as session:
        lineage = make_lineage(
            references["target_version_id"],
            job_id=references["job_id"],
        )

        session.add(lineage)
        session.commit()

        persisted = session.get(
            DatasetVersionLineageORM,
            lineage.lineage_id,
        )

        assert persisted is not None
        assert persisted.source_type == DatasetLineageSourceType.INGESTION_JOB
        assert persisted.relationship_type == DatasetLineageRelationship.INGESTED_FROM
        assert persisted.ingestion_job_id == references["job_id"]
        assert persisted.source_dataset_version_id is None


def test_dataset_version_lineage_can_be_persisted(engine, references):
    with Session(engine) as session:
        lineage = make_lineage(
            references["target_version_id"],
            source_type=DatasetLineageSourceType.DATASET_VERSION,
            source_version_id=references["source_version_id"],
            relationship=DatasetLineageRelationship.DERIVED_FROM,
        )

        session.add(lineage)
        session.commit()

        persisted = session.get(
            DatasetVersionLineageORM,
            lineage.lineage_id,
        )

        assert persisted is not None
        assert persisted.source_type == DatasetLineageSourceType.DATASET_VERSION
        assert persisted.relationship_type == DatasetLineageRelationship.DERIVED_FROM
        assert persisted.source_dataset_version_id == references["source_version_id"]
        assert persisted.ingestion_job_id is None


def test_ingestion_lineage_rejects_wrong_relationship(engine, references):
    with Session(engine) as session:
        session.add(
            make_lineage(
                references["target_version_id"],
                job_id=references["job_id"],
                relationship=DatasetLineageRelationship.DERIVED_FROM,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_dataset_version_lineage_rejects_wrong_relationship(
    engine,
    references,
):
    with Session(engine) as session:
        session.add(
            make_lineage(
                references["target_version_id"],
                source_type=DatasetLineageSourceType.DATASET_VERSION,
                source_version_id=references["source_version_id"],
                relationship=DatasetLineageRelationship.INGESTED_FROM,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_ingestion_lineage_rejects_missing_job(engine, references):
    with Session(engine) as session:
        session.add(
            make_lineage(
                references["target_version_id"],
                job_id=uuid4(),
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_dataset_version_lineage_rejects_missing_source_version(
    engine,
    references,
):
    with Session(engine) as session:
        session.add(
            make_lineage(
                references["target_version_id"],
                source_type=DatasetLineageSourceType.DATASET_VERSION,
                source_version_id=uuid4(),
                relationship=DatasetLineageRelationship.DERIVED_FROM,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_lineage_rejects_missing_target_version(engine, references):
    with Session(engine) as session:
        session.add(
            make_lineage(
                uuid4(),
                job_id=references["job_id"],
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_lineage_rejects_self_reference(engine, references):
    with Session(engine) as session:
        version_id = references["target_version_id"]

        session.add(
            make_lineage(
                version_id,
                source_type=DatasetLineageSourceType.DATASET_VERSION,
                source_version_id=version_id,
                relationship=DatasetLineageRelationship.DERIVED_FROM,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_lineage_rejects_both_source_references(engine, references):
    with Session(engine) as session:
        session.add(
            DatasetVersionLineageORM(
                lineage_id=uuid4(),
                target_dataset_version_id=references["target_version_id"],
                source_type=DatasetLineageSourceType.INGESTION_JOB,
                ingestion_job_id=references["job_id"],
                source_dataset_version_id=references["source_version_id"],
                relationship_type=DatasetLineageRelationship.INGESTED_FROM,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_lineage_rejects_missing_source_reference(engine, references):
    with Session(engine) as session:
        session.add(
            DatasetVersionLineageORM(
                lineage_id=uuid4(),
                target_dataset_version_id=references["target_version_id"],
                source_type=DatasetLineageSourceType.INGESTION_JOB,
                ingestion_job_id=None,
                source_dataset_version_id=None,
                relationship_type=DatasetLineageRelationship.INGESTED_FROM,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()
