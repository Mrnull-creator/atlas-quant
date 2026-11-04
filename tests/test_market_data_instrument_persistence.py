"""Tests for the Market Data Service instrument ORM mapping."""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)
from atlas_quant.services.market_data.infrastructure.persistence.instrument import (
    InstrumentORM,
)


@pytest.fixture
def engine():
    """Create an isolated in-memory database for each test."""

    database_engine = create_engine("sqlite:///:memory:")
    MarketDataBase.metadata.create_all(database_engine)

    yield database_engine

    database_engine.dispose()


def test_instrument_table_is_registered():
    assert "instruments" in MarketDataBase.metadata.tables


def test_instrument_primary_key_is_uuid():
    column = InstrumentORM.__table__.c.instrument_id

    assert column.primary_key is True
    assert column.type.python_type is UUID


def test_instrument_business_key_constraint():
    constraint = next(
        constraint
        for constraint in InstrumentORM.__table__.constraints
        if constraint.name == "uq_instruments_business_key"
    )

    assert [column.name for column in constraint.columns] == [
        "symbol",
        "asset_class",
        "exchange",
    ]
    assert (
        constraint.dialect_options["postgresql"]["nulls_not_distinct"]
        is True
    )


def test_instrument_precision_and_decimal_constraints_exist():
    constraint_names = {
        constraint.name
        for constraint in InstrumentORM.__table__.constraints
    }

    assert "ck_instruments_price_precision_nonnegative" in constraint_names
    assert "ck_instruments_quantity_precision_nonnegative" in constraint_names
    assert "ck_instruments_tick_size_positive" in constraint_names
    assert "ck_instruments_contract_size_positive" in constraint_names


def test_instrument_enum_uses_domain_values():
    column = InstrumentORM.__table__.c.asset_class

    assert column.type.enums == [
        asset_class.value for asset_class in AssetClass
    ]


def test_instrument_defaults_and_uuid_persist(engine):
    with Session(engine) as session:
        instrument = InstrumentORM(
            symbol="EURUSD",
            asset_class=AssetClass.FOREX,
        )

        session.add(instrument)
        session.flush()

        assert isinstance(instrument.instrument_id, UUID)
        assert instrument.price_precision == 5
        assert instrument.quantity_precision == 2
        assert instrument.is_active is True

        session.commit()

        persisted = session.get(
            InstrumentORM,
            instrument.instrument_id,
        )

        assert persisted is not None
        assert persisted.symbol == "EURUSD"
        assert persisted.asset_class is AssetClass.FOREX


def test_instrument_decimal_values_round_trip(engine):
    with Session(engine) as session:
        instrument = InstrumentORM(
            symbol="EURUSD",
            asset_class=AssetClass.FOREX,
            tick_size=Decimal("0.00001"),
            contract_size=Decimal("100000"),
        )

        session.add(instrument)
        session.commit()

        persisted = session.get(
            InstrumentORM,
            instrument.instrument_id,
        )

        assert persisted is not None
        assert persisted.tick_size == Decimal("0.00001")
        assert persisted.contract_size == Decimal("100000")


def test_instrument_business_key_rejects_duplicate_non_null_exchange(
    engine,
):
    with Session(engine) as session:
        session.add(
            InstrumentORM(
                symbol="AAPL",
                asset_class=AssetClass.EQUITY,
                exchange="NASDAQ",
            )
        )
        session.commit()

        session.add(
            InstrumentORM(
                symbol="AAPL",
                asset_class=AssetClass.EQUITY,
                exchange="NASDAQ",
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_instrument_business_key_allows_different_exchanges(engine):
    with Session(engine) as session:
        session.add_all(
            [
                InstrumentORM(
                    symbol="ABC",
                    asset_class=AssetClass.EQUITY,
                    exchange="NYSE",
                ),
                InstrumentORM(
                    symbol="ABC",
                    asset_class=AssetClass.EQUITY,
                    exchange="NASDAQ",
                ),
            ]
        )

        session.commit()

        instruments = session.scalars(
            select(InstrumentORM)
        ).all()

        assert len(instruments) == 2
