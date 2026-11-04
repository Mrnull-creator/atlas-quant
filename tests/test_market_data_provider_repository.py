"""Tests for the asynchronous market data provider repository."""

import asyncio

import pytest
from sqlalchemy.exc import IntegrityError

from atlas_quant.services.market_data.application.repository_errors import (
    ProviderRepositoryDuplicateError,
    ProviderRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider import (
    ProviderORM,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider_repository import (
    SQLAlchemyProviderRepository,
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


def make_provider(**overrides):
    values = {
        "provider_id": "dukascopy",
        "name": "Dukascopy",
        "supported_asset_classes": (AssetClass.FOREX,),
        "supported_timeframes": (
            Timeframe.M1,
            Timeframe.H1,
        ),
        "supports_historical_data": True,
        "supports_quote_data": True,
        "is_enabled": True,
    }
    values.update(overrides)

    return MarketDataProvider(**values)


def make_orm_provider(**overrides):
    values = {
        "provider_id": "dukascopy",
        "name": "Dukascopy",
        "supported_asset_classes": ["forex"],
        "supported_timeframes": ["1m", "1h"],
        "supports_historical_data": True,
        "supports_quote_data": True,
        "is_enabled": True,
    }
    values.update(overrides)

    return ProviderORM(**values)


def make_repository(session):
    return SQLAlchemyProviderRepository(
        FakeSessionFactory(session)
    )


def test_get_by_id_returns_domain_provider():
    orm_provider = make_orm_provider()
    session = FakeSession()
    session.get_result = orm_provider

    repository = make_repository(session)

    result = run(repository.get_by_id("dukascopy"))

    assert result is not None
    assert result.provider_id == "dukascopy"
    assert result.name == "Dukascopy"
    assert result.supported_asset_classes == (AssetClass.FOREX,)
    assert result.supported_timeframes == (
        Timeframe.M1,
        Timeframe.H1,
    )
    assert result.supports_historical_data is True
    assert result.supports_quote_data is True
    assert result.is_enabled is True


def test_get_by_id_normalizes_provider_id():
    orm_provider = make_orm_provider()
    session = FakeSession()
    session.get_result = orm_provider

    repository = make_repository(session)

    result = run(repository.get_by_id(" DUKASCOPY "))

    assert result is not None
    assert result.provider_id == "dukascopy"


def test_get_by_id_returns_none_when_missing():
    session = FakeSession()
    repository = make_repository(session)

    result = run(repository.get_by_id("unknown"))

    assert result is None


def test_list_all_returns_domain_providers_in_identifier_order():
    first = make_orm_provider(
        provider_id="alpha",
        name="Alpha",
    )
    second = make_orm_provider(
        provider_id="zulu",
        name="Zulu",
    )

    session = FakeSession()
    session.result = FakeResult([first, second])

    repository = make_repository(session)

    result = run(repository.list_all())

    assert len(result) == 2
    assert result[0].provider_id == "alpha"
    assert result[1].provider_id == "zulu"
    assert "ORDER BY market_data_providers.provider_id" in str(
        session.executed_statement
    )


def test_add_converts_domain_provider_to_orm():
    provider = make_provider()
    session = FakeSession()
    repository = make_repository(session)

    run(repository.add(provider))

    assert len(session.added) == 1

    persisted = session.added[0]

    assert isinstance(persisted, ProviderORM)
    assert persisted.provider_id == provider.provider_id
    assert persisted.name == provider.name
    assert persisted.supported_asset_classes == ["forex"]
    assert persisted.supported_timeframes == ["1m", "1h"]
    assert persisted.supports_historical_data is True
    assert persisted.supports_quote_data is True
    assert persisted.is_enabled is True


def test_add_preserves_quote_only_provider_capabilities():
    provider = make_provider(
        provider_id="quote-feed",
        name="Quote Feed",
        supported_timeframes=(),
        supports_historical_data=False,
        supports_quote_data=True,
    )
    session = FakeSession()
    repository = make_repository(session)

    run(repository.add(provider))

    persisted = session.added[0]

    assert persisted.supported_timeframes == []
    assert persisted.supports_historical_data is False
    assert persisted.supports_quote_data is True


def test_update_changes_provider_fields_without_changing_identifier():
    existing = make_orm_provider()
    updated = make_provider(
        name="Dukascopy Updated",
        supported_timeframes=(Timeframe.M5,),
        supports_quote_data=False,
        is_enabled=False,
    )

    session = FakeSession()
    session.get_result = existing
    repository = make_repository(session)

    run(repository.update(updated))

    assert existing.provider_id == updated.provider_id
    assert existing.name == "Dukascopy Updated"
    assert existing.supported_timeframes == ["5m"]
    assert existing.supports_quote_data is False
    assert existing.is_enabled is False


def test_update_rejects_missing_provider():
    session = FakeSession()
    repository = make_repository(session)

    with pytest.raises(ProviderRepositoryRecordNotFoundError):
        run(repository.update(make_provider()))


def test_add_translates_provider_identifier_integrity_error():
    session = FakeSession()
    session.flush_error = IntegrityError(
        "INSERT",
        {},
        Exception(
            "UNIQUE constraint failed: "
            "market_data_providers.provider_id"
        ),
    )

    repository = make_repository(session)

    with pytest.raises(ProviderRepositoryDuplicateError):
        run(repository.add(make_provider()))


def test_add_does_not_translate_unrelated_integrity_errors():
    session = FakeSession()
    session.flush_error = IntegrityError(
        "INSERT",
        {},
        Exception("some other integrity failure"),
    )

    repository = make_repository(session)

    with pytest.raises(IntegrityError):
        run(repository.add(make_provider()))
