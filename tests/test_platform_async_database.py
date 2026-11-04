"""Tests for asynchronous database infrastructure."""

import asyncio
from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from atlas_quant.platform import database
from atlas_quant.platform.config import Settings
from atlas_quant.platform.errors import ConfigurationError


DATABASE_URL = "postgresql+psycopg://user:password@localhost/test_db"


def test_create_async_database_engine_uses_async_engine(monkeypatch):
    expected_engine = object()
    create_engine = Mock(return_value=expected_engine)

    monkeypatch.setattr(
        database,
        "create_async_engine",
        create_engine,
    )

    result = database.create_async_database_engine(
        DATABASE_URL,
        echo=True,
    )

    assert result is expected_engine
    create_engine.assert_called_once_with(
        DATABASE_URL,
        echo=True,
        pool_pre_ping=True,
    )


def test_create_async_database_engine_accepts_secret_str(monkeypatch):
    create_engine = Mock(return_value=object())

    monkeypatch.setattr(
        database,
        "create_async_engine",
        create_engine,
    )

    secret_url = SecretStr(DATABASE_URL)

    database.create_async_database_engine(secret_url)

    create_engine.assert_called_once_with(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
    )


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        " ",
    ],
)
def test_create_async_database_engine_rejects_empty_url(database_url):
    with pytest.raises(ConfigurationError):
        database.create_async_database_engine(database_url)


def test_create_async_engine_from_settings(monkeypatch):
    expected_engine = object()
    create_engine = Mock(return_value=expected_engine)

    monkeypatch.setattr(
        database,
        "create_async_engine",
        create_engine,
    )

    settings = Settings(database_url=SecretStr(DATABASE_URL))

    result = database.create_async_engine_from_settings(settings)

    assert result is expected_engine
    create_engine.assert_called_once_with(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
    )


def test_create_async_engine_from_settings_requires_database_url():
    settings = Settings(database_url=None)

    with pytest.raises(ConfigurationError):
        database.create_async_engine_from_settings(settings)


def test_create_async_session_factory_configures_session_options():
    engine = database.create_async_database_engine(DATABASE_URL)

    try:
        session_factory = database.create_async_session_factory(engine)
        session = session_factory()

        assert session.bind is engine
        assert session.autoflush is False
        assert session.sync_session.expire_on_commit is False

        asyncio.run(session.close())
    finally:
        asyncio.run(engine.dispose())
