"""Tests for the asynchronous instrument repository."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from atlas_quant.services.market_data.application.repository_errors import (
    InstrumentBusinessKeyConflictError,
    InstrumentRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.infrastructure.persistence.instrument import (
    InstrumentORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.instrument_repository import (
    SQLAlchemyInstrumentRepository,
)


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class FakeResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class FakeSession:
    def __init__(self):
        self.result = FakeResult()
        self.get_result = None
        self.added = []
        self.flush_error = None
        self.executed_statement = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    def begin(self):
        return FakeTransaction()

    async def execute(self, statement):
        self.executed_statement = statement
        return self.result

    async def get(self, model, identity):
        return self.get_result

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        if self.flush_error is not None:
            raise self.flush_error


class FakeSessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self.session


def run(coroutine):
    return asyncio.run(coroutine)


def make_orm_instrument(**overrides):
    values = {
        "instrument_id": uuid4(),
        "symbol": "EURUSD",
        "asset_class": AssetClass.FOREX,
        "exchange": None,
        "base_currency": "EUR",
        "quote_currency": "USD",
        "price_precision": 5,
        "quantity_precision": 2,
        "tick_size": Decimal("0.00001"),
        "contract_size": Decimal("100000"),
        "is_active": True,
    }
    values.update(overrides)

    return InstrumentORM(**values)


def make_repository(session):
    return SQLAlchemyInstrumentRepository(
        FakeSessionFactory(session)
    )


def test_get_by_id_returns_domain_instrument():
    orm_instrument = make_orm_instrument()
    session = FakeSession()
    session.get_result = orm_instrument

    repository = make_repository(session)

    result = run(
        repository.get_by_id(orm_instrument.instrument_id)
    )

    assert result is not None
    assert result.instrument_id == orm_instrument.instrument_id
    assert result.symbol == "EURUSD"
    assert result.asset_class is AssetClass.FOREX
    assert result.tick_size == Decimal("0.00001")


def test_get_by_id_returns_none_when_missing():
    session = FakeSession()
    repository = make_repository(session)

    result = run(repository.get_by_id(uuid4()))

    assert result is None


def test_get_by_business_key_supports_null_exchange():
    orm_instrument = make_orm_instrument()
    session = FakeSession()
    session.result = FakeResult(orm_instrument)

    repository = make_repository(session)

    result = run(
        repository.get_by_business_key(
            " eurusd ",
            AssetClass.FOREX,
            None,
        )
    )

    assert result is not None
    assert result.instrument_id == orm_instrument.instrument_id
    assert "instruments.exchange IS NULL" in str(
        session.executed_statement
    )


def test_get_by_business_key_supports_explicit_exchange():
    orm_instrument = make_orm_instrument(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        exchange="NASDAQ",
    )
    session = FakeSession()
    session.result = FakeResult(orm_instrument)

    repository = make_repository(session)

    result = run(
        repository.get_by_business_key(
            "aapl",
            AssetClass.EQUITY,
            "nasdaq",
        )
    )

    assert result is not None
    assert result.symbol == "AAPL"
    assert "instruments.exchange =" in str(
        session.executed_statement
    )


def test_list_all_returns_domain_instruments():
    first = make_orm_instrument(symbol="EURUSD")
    second = make_orm_instrument(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
    )

    session = FakeSession()
    session.result = FakeResult([first, second])

    repository = make_repository(session)

    result = run(repository.list_all())

    assert len(result) == 2
    assert result[0].symbol == "EURUSD"
    assert result[1].symbol == "AAPL"


def test_add_converts_domain_instrument_to_orm():
    instrument = Instrument(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        tick_size=Decimal("0.00001"),
        contract_size=Decimal("100000"),
    )
    session = FakeSession()
    repository = make_repository(session)

    run(repository.add(instrument))

    assert len(session.added) == 1

    persisted = session.added[0]

    assert isinstance(persisted, InstrumentORM)
    assert persisted.instrument_id == instrument.instrument_id
    assert persisted.symbol == instrument.symbol
    assert persisted.asset_class is instrument.asset_class
    assert persisted.tick_size == instrument.tick_size


def test_update_changes_fields_without_changing_uuid():
    existing = make_orm_instrument()
    updated = Instrument(
        instrument_id=existing.instrument_id,
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        exchange="DUKASCOPY",
        base_currency="EUR",
        quote_currency="USD",
        price_precision=6,
        quantity_precision=2,
        tick_size=Decimal("0.000001"),
        contract_size=Decimal("100000"),
        is_active=False,
    )

    session = FakeSession()
    session.get_result = existing
    repository = make_repository(session)

    run(repository.update(updated))

    assert existing.instrument_id == updated.instrument_id
    assert existing.exchange == "DUKASCOPY"
    assert existing.price_precision == 6
    assert existing.tick_size == Decimal("0.000001")
    assert existing.is_active is False


def test_update_rejects_missing_instrument():
    session = FakeSession()
    repository = make_repository(session)

    with pytest.raises(InstrumentRepositoryRecordNotFoundError):
        run(
            repository.update(
                Instrument(
                    symbol="EURUSD",
                    asset_class=AssetClass.FOREX,
                )
            )
        )


def test_add_translates_business_key_integrity_error():
    session = FakeSession()

    original_error = Exception(
        "UNIQUE constraint failed: "
        "instruments.symbol, instruments.asset_class, instruments.exchange"
    )
    session.flush_error = IntegrityError(
        "INSERT",
        {},
        original_error,
    )

    repository = make_repository(session)

    with pytest.raises(InstrumentBusinessKeyConflictError):
        run(
            repository.add(
                Instrument(
                    symbol="EURUSD",
                    asset_class=AssetClass.FOREX,
                )
            )
        )


def test_add_does_not_translate_unrelated_integrity_errors():
    session = FakeSession()
    session.flush_error = IntegrityError(
        "INSERT",
        {},
        Exception("some other integrity failure"),
    )

    repository = make_repository(session)

    with pytest.raises(IntegrityError):
        run(
            repository.add(
                Instrument(
                    symbol="EURUSD",
                    asset_class=AssetClass.FOREX,
                )
            )
        )


def test_update_translates_business_key_integrity_error():
    existing = make_orm_instrument()
    updated = Instrument(
        instrument_id=existing.instrument_id,
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        exchange="DUKASCOPY",
    )

    session = FakeSession()
    session.get_result = existing
    session.flush_error = IntegrityError(
        "UPDATE",
        {},
        Exception(
            "UNIQUE constraint failed: "
            "instruments.symbol, instruments.asset_class, instruments.exchange"
        ),
    )

    repository = make_repository(session)

    with pytest.raises(InstrumentBusinessKeyConflictError):
        run(repository.update(updated))
