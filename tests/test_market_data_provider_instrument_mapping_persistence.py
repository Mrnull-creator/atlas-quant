"""Tests for provider instrument mapping ORM persistence."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
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
    """Create an isolated SQLite database with foreign keys enabled."""

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
def reference_records(engine):
    """Create the instrument and provider referenced by test mappings."""

    with Session(engine) as session:
        instrument = InstrumentORM(
            symbol="EURUSD",
            asset_class=AssetClass.FOREX,
        )
        provider = ProviderORM(
            provider_id="dukascopy",
            name="Dukascopy",
            supported_asset_classes=["forex"],
            supported_timeframes=["1m", "1h"],
            supports_historical_data=True,
            supports_quote_data=True,
            is_enabled=True,
        )

        session.add_all([instrument, provider])
        session.commit()

        return instrument.instrument_id, provider.provider_id


def make_mapping(instrument_id, provider_id, **overrides):
    """Create a mapping ORM instance."""

    values = {
        "instrument_id": instrument_id,
        "provider_id": provider_id,
        "provider_symbol": "EURUSD",
        "provider_exchange": "",
        "is_active": True,
    }
    values.update(overrides)

    return ProviderInstrumentMappingORM(**values)


def test_mapping_table_is_registered():
    assert "provider_instrument_mappings" in MarketDataBase.metadata.tables


def test_mapping_primary_key_is_uuid():
    column = ProviderInstrumentMappingORM.__table__.c.mapping_id

    assert column.primary_key is True
    assert column.type.python_type is UUID


def test_mapping_foreign_keys_are_restrictive():
    foreign_keys = {
        foreign_key.parent.name: foreign_key
        for foreign_key in ProviderInstrumentMappingORM.__table__.foreign_keys
    }

    assert set(foreign_keys) == {"instrument_id", "provider_id"}
    assert foreign_keys["instrument_id"].ondelete == "RESTRICT"
    assert foreign_keys["provider_id"].ondelete == "RESTRICT"


def test_active_identifier_index_is_partial_and_unique():
    index = next(
        index
        for index in ProviderInstrumentMappingORM.__table__.indexes
        if index.name
        == "uq_provider_instrument_mappings_active_provider_identifier"
    )

    assert index.unique is True
    assert [column.name for column in index.columns] == [
        "provider_id",
        "provider_symbol",
        "provider_exchange",
    ]
    assert index.dialect_options["sqlite"]["where"] is not None
    assert index.dialect_options["postgresql"]["where"] is not None


def test_mapping_defaults_and_uuid_persist(engine, reference_records):
    instrument_id, provider_id = reference_records

    with Session(engine) as session:
        mapping = make_mapping(instrument_id, provider_id)
        session.add(mapping)
        session.flush()

        assert isinstance(mapping.mapping_id, UUID)
        assert mapping.is_active is True
        assert mapping.provider_exchange == ""

        session.commit()

        persisted = session.get(
            ProviderInstrumentMappingORM,
            mapping.mapping_id,
        )

        assert persisted is not None
        assert persisted.instrument_id == instrument_id
        assert persisted.provider_id == provider_id


def test_active_provider_identifier_rejects_duplicate(
    engine,
    reference_records,
):
    instrument_id, provider_id = reference_records

    with Session(engine) as session:
        session.add(make_mapping(instrument_id, provider_id))
        session.commit()

        session.add(make_mapping(instrument_id, provider_id))

        with pytest.raises(IntegrityError):
            session.commit()


def test_inactive_mapping_allows_identifier_reuse(
    engine,
    reference_records,
):
    instrument_id, provider_id = reference_records

    with Session(engine) as session:
        retired_mapping = make_mapping(
            instrument_id,
            provider_id,
            is_active=False,
        )
        replacement_mapping = make_mapping(
            instrument_id,
            provider_id,
        )

        session.add_all([retired_mapping, replacement_mapping])
        session.commit()

        mappings = session.scalars(
            select(ProviderInstrumentMappingORM)
        ).all()

        assert len(mappings) == 2
        assert sum(mapping.is_active for mapping in mappings) == 1


def test_deactivating_mapping_allows_new_active_mapping(
    engine,
    reference_records,
):
    instrument_id, provider_id = reference_records

    with Session(engine) as session:
        original = make_mapping(instrument_id, provider_id)
        session.add(original)
        session.commit()

        original.is_active = False

        replacement = make_mapping(instrument_id, provider_id)
        session.add(replacement)
        session.commit()

        assert original.is_active is False
        assert replacement.is_active is True


def test_different_provider_exchanges_allow_same_symbol(
    engine,
    reference_records,
):
    instrument_id, provider_id = reference_records

    with Session(engine) as session:
        session.add_all(
            [
                make_mapping(
                    instrument_id,
                    provider_id,
                    provider_exchange="FX",
                ),
                make_mapping(
                    instrument_id,
                    provider_id,
                    provider_exchange="CFD",
                ),
            ]
        )
        session.commit()

        mappings = session.scalars(
            select(ProviderInstrumentMappingORM)
        ).all()

        assert len(mappings) == 2


def test_mapping_rejects_unknown_instrument(engine, reference_records):
    _, provider_id = reference_records

    with Session(engine) as session:
        session.add(make_mapping(uuid4(), provider_id))

        with pytest.raises(IntegrityError):
            session.commit()


def test_mapping_rejects_unknown_provider(engine, reference_records):
    instrument_id, _ = reference_records

    with Session(engine) as session:
        session.add(make_mapping(instrument_id, "unknown-provider"))

        with pytest.raises(IntegrityError):
            session.commit()