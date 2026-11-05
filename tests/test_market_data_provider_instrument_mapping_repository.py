"""Tests for the asynchronous provider instrument mapping repository."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from atlas_quant.services.market_data.application.repository_errors import (
    ProviderInstrumentMappingConflictError,
    ProviderInstrumentMappingRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.provider_instrument_mappings import (
    ProviderInstrumentMapping,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider_instrument_mapping import (
    ProviderInstrumentMappingORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider_instrument_mapping_repository import (
    SQLAlchemyProviderInstrumentMappingRepository,
)


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class FakeResult:
    def __init__(self, value=None):
        self.value = value

    def scalars(self):
        return self

    def all(self):
        return self.value

    def scalar_one_or_none(self):
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


def make_mapping(**overrides):
    values = {
        "mapping_id": uuid4(),
        "instrument_id": uuid4(),
        "provider_id": "dukascopy",
        "provider_symbol": "EURUSD",
        "provider_exchange": None,
        "is_active": True,
    }
    values.update(overrides)

    return ProviderInstrumentMapping(**values)


def make_orm_mapping(**overrides):
    values = {
        "mapping_id": uuid4(),
        "instrument_id": uuid4(),
        "provider_id": "dukascopy",
        "provider_symbol": "EURUSD",
        "provider_exchange": "",
        "is_active": True,
    }
    values.update(overrides)

    return ProviderInstrumentMappingORM(**values)


def make_repository(session):
    return SQLAlchemyProviderInstrumentMappingRepository(
        FakeSessionFactory(session)
    )


def test_get_by_id_returns_domain_mapping():
    orm_mapping = make_orm_mapping()
    session = FakeSession()
    session.get_result = orm_mapping

    repository = make_repository(session)

    result = run(repository.get_by_id(orm_mapping.mapping_id))

    assert result is not None
    assert result.mapping_id == orm_mapping.mapping_id
    assert result.instrument_id == orm_mapping.instrument_id
    assert result.provider_id == "dukascopy"
    assert result.provider_symbol == "EURUSD"
    assert result.provider_exchange is None
    assert result.is_active is True


def test_get_by_id_returns_none_when_missing():
    session = FakeSession()
    repository = make_repository(session)

    result = run(repository.get_by_id(uuid4()))

    assert result is None


def test_get_by_provider_identifier_normalizes_lookup_values():
    orm_mapping = make_orm_mapping()
    session = FakeSession()
    session.result = FakeResult(orm_mapping)

    repository = make_repository(session)

    result = run(
        repository.get_by_provider_identifier(
            " DUKASCOPY ",
            " EURUSD ",
        )
    )

    assert result is not None
    assert result.mapping_id == orm_mapping.mapping_id

    statement = str(session.executed_statement)

    assert "provider_instrument_mappings.provider_id" in statement
    assert "provider_instrument_mappings.provider_symbol" in statement
    assert "provider_instrument_mappings.is_active IS true" in statement


def test_get_by_provider_identifier_normalizes_exchange():
    orm_mapping = make_orm_mapping(provider_exchange="FX")
    session = FakeSession()
    session.result = FakeResult(orm_mapping)

    repository = make_repository(session)

    result = run(
        repository.get_by_provider_identifier(
            "dukascopy",
            "EURUSD",
            " FX ",
        )
    )

    assert result is not None
    assert result.provider_exchange == "FX"


def test_get_by_provider_identifier_returns_none_when_missing():
    session = FakeSession()
    repository = make_repository(session)

    result = run(
        repository.get_by_provider_identifier(
            "dukascopy",
            "UNKNOWN",
        )
    )

    assert result is None


def test_list_by_instrument_id_returns_active_and_inactive_mappings():
    instrument_id = uuid4()

    active = make_orm_mapping(
        instrument_id=instrument_id,
        provider_symbol="EURUSD",
        is_active=True,
    )
    inactive = make_orm_mapping(
        instrument_id=instrument_id,
        provider_symbol="GBPUSD",
        is_active=False,
    )

    session = FakeSession()
    session.result = FakeResult([active, inactive])

    repository = make_repository(session)

    result = run(repository.list_by_instrument_id(instrument_id))

    assert len(result) == 2
    assert result[0].provider_symbol == "EURUSD"
    assert result[0].is_active is True
    assert result[1].provider_symbol == "GBPUSD"
    assert result[1].is_active is False

    assert "ORDER BY" in str(session.executed_statement)


def test_list_by_provider_id_normalizes_provider_id():
    first = make_orm_mapping(provider_symbol="EURUSD")
    second = make_orm_mapping(
        provider_symbol="GBPUSD",
        is_active=False,
    )

    session = FakeSession()
    session.result = FakeResult([first, second])

    repository = make_repository(session)

    result = run(repository.list_by_provider_id(" DUKASCOPY "))

    assert len(result) == 2
    assert result[0].provider_symbol == "EURUSD"
    assert result[1].provider_symbol == "GBPUSD"
    assert "ORDER BY" in str(session.executed_statement)


def test_add_converts_domain_mapping_to_orm():
    mapping = make_mapping(provider_exchange="FX")
    session = FakeSession()
    repository = make_repository(session)

    run(repository.add(mapping))

    assert len(session.added) == 1

    persisted = session.added[0]

    assert isinstance(persisted, ProviderInstrumentMappingORM)
    assert persisted.mapping_id == mapping.mapping_id
    assert persisted.instrument_id == mapping.instrument_id
    assert persisted.provider_id == mapping.provider_id
    assert persisted.provider_symbol == mapping.provider_symbol
    assert persisted.provider_exchange == "FX"
    assert persisted.is_active is True


def test_add_persists_missing_exchange_as_empty_string():
    mapping = make_mapping(provider_exchange=None)
    session = FakeSession()
    repository = make_repository(session)

    run(repository.add(mapping))

    assert session.added[0].provider_exchange == ""


def test_add_translates_active_identifier_conflict():
    session = FakeSession()
    session.flush_error = IntegrityError(
        "INSERT",
        {},
        Exception(
            "UNIQUE constraint failed: "
            "provider_instrument_mappings.provider_id, "
            "provider_instrument_mappings.provider_symbol, "
            "provider_instrument_mappings.provider_exchange"
        ),
    )

    repository = make_repository(session)

    with pytest.raises(ProviderInstrumentMappingConflictError):
        run(repository.add(make_mapping()))


def test_add_does_not_translate_unrelated_integrity_errors():
    session = FakeSession()
    session.flush_error = IntegrityError(
        "INSERT",
        {},
        Exception("some other integrity failure"),
    )

    repository = make_repository(session)

    with pytest.raises(IntegrityError):
        run(repository.add(make_mapping()))


def test_update_changes_active_status_without_changing_identity():
    mapping = make_mapping(is_active=False)

    existing = make_orm_mapping(
        mapping_id=mapping.mapping_id,
        instrument_id=mapping.instrument_id,
        provider_id=mapping.provider_id,
        provider_symbol=mapping.provider_symbol,
        provider_exchange="",
        is_active=True,
    )

    session = FakeSession()
    session.get_result = existing

    repository = make_repository(session)

    run(repository.update(mapping))

    assert existing.mapping_id == mapping.mapping_id
    assert existing.instrument_id == mapping.instrument_id
    assert existing.provider_id == mapping.provider_id
    assert existing.provider_symbol == mapping.provider_symbol
    assert existing.is_active is False


def test_update_rejects_missing_mapping():
    session = FakeSession()
    repository = make_repository(session)

    with pytest.raises(ProviderInstrumentMappingRecordNotFoundError):
        run(repository.update(make_mapping()))


def test_update_rejects_changes_to_mapping_identity():
    mapping = make_mapping()

    existing = make_orm_mapping(
        mapping_id=mapping.mapping_id,
        instrument_id=mapping.instrument_id,
        provider_id=mapping.provider_id,
        provider_symbol=mapping.provider_symbol,
        provider_exchange="",
    )

    changed_mapping = ProviderInstrumentMapping(
        mapping_id=mapping.mapping_id,
        instrument_id=uuid4(),
        provider_id=mapping.provider_id,
        provider_symbol=mapping.provider_symbol,
        provider_exchange=None,
        is_active=False,
    )

    session = FakeSession()
    session.get_result = existing

    repository = make_repository(session)

    with pytest.raises(ValueError, match="identity is immutable"):
        run(repository.update(changed_mapping))


def test_update_translates_active_identifier_conflict():
    mapping = make_mapping(is_active=True)

    existing = make_orm_mapping(
        mapping_id=mapping.mapping_id,
        instrument_id=mapping.instrument_id,
        provider_id=mapping.provider_id,
        provider_symbol=mapping.provider_symbol,
        provider_exchange="",
        is_active=False,
    )

    session = FakeSession()
    session.get_result = existing
    session.flush_error = IntegrityError(
        "UPDATE",
        {},
        Exception(
            "UNIQUE constraint failed: "
            "provider_instrument_mappings.provider_id, "
            "provider_instrument_mappings.provider_symbol, "
            "provider_instrument_mappings.provider_exchange"
        ),
    )

    repository = make_repository(session)

    with pytest.raises(ProviderInstrumentMappingConflictError):
        run(repository.update(mapping))