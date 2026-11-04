import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from atlas_quant.platform.config import Settings
from atlas_quant.platform.database import (
    create_database_engine,
    create_engine_from_settings,
    create_session_factory,
    session_scope,
)
from atlas_quant.platform.errors import ConfigurationError


def test_create_database_engine():
    engine = create_database_engine("sqlite+pysqlite:///:memory:")

    try:
        assert isinstance(engine, Engine)

        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()


def test_create_database_engine_accepts_secret_string():
    engine = create_database_engine(
        SecretStr("sqlite+pysqlite:///:memory:")
    )

    try:
        assert isinstance(engine, Engine)
    finally:
        engine.dispose()


def test_create_database_engine_rejects_empty_url():
    with pytest.raises(ConfigurationError):
        create_database_engine("")


def test_engine_from_settings():
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        _env_file=None,
    )

    engine = create_engine_from_settings(settings)

    try:
        assert isinstance(engine, Engine)
    finally:
        engine.dispose()


def test_engine_from_settings_requires_database_url():
    settings = Settings(_env_file=None)

    with pytest.raises(
        ConfigurationError,
        match="Database URL is not configured",
    ):
        create_engine_from_settings(settings)


def test_session_factory_creates_sessions():
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    factory = create_session_factory(engine)

    try:
        with session_scope(factory) as session:
            assert isinstance(session, Session)
            assert session.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()


def test_session_scope_preserves_explicit_commit():
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    factory = create_session_factory(engine)

    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE example (value INTEGER)")
            )

        with session_scope(factory) as session:
            session.execute(
                text("INSERT INTO example (value) VALUES (42)")
            )
            session.commit()

        with session_scope(factory) as session:
            result = session.execute(
                text("SELECT value FROM example")
            ).scalar_one()

        assert result == 42
    finally:
        engine.dispose()


def test_session_scope_rolls_back_on_exception():
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    factory = create_session_factory(engine)

    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE example (value INTEGER)")
            )

        with pytest.raises(RuntimeError):
            with session_scope(factory) as session:
                session.execute(
                    text("INSERT INTO example (value) VALUES (42)")
                )
                raise RuntimeError("Simulated failure")

        with session_scope(factory) as session:
            count = session.execute(
                text("SELECT COUNT(*) FROM example")
            ).scalar_one()

        assert count == 0
    finally:
        engine.dispose()
