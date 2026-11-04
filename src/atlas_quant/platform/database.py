"""Shared SQLAlchemy engine and session management."""

from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from atlas_quant.platform.config import Settings, get_settings
from atlas_quant.platform.errors import ConfigurationError


def _resolve_database_url(database_url: SecretStr | str) -> str:
    """Return a validated database URL without exposing it in error messages."""
    url = (
        database_url.get_secret_value()
        if isinstance(database_url, SecretStr)
        else database_url
    )

    if not isinstance(url, str) or not url.strip():
        raise ConfigurationError("Database URL must be configured.")

    return url


def create_database_engine(
    database_url: SecretStr | str,
    *,
    echo: bool = False,
) -> Engine:
    """Create a synchronous SQLAlchemy engine."""
    url = _resolve_database_url(database_url)

    return create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
    )


def create_async_database_engine(
    database_url: SecretStr | str,
    *,
    echo: bool = False,
) -> AsyncEngine:
    """Create an asynchronous SQLAlchemy engine."""
    url = _resolve_database_url(database_url)

    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
    )


def create_engine_from_settings(
    settings: Settings | None = None,
) -> Engine:
    """Create a synchronous engine using application settings."""
    resolved_settings = settings or get_settings()

    if resolved_settings.database_url is None:
        raise ConfigurationError(
            "Database URL is not configured. Set ATLAS_DATABASE_URL."
        )

    return create_database_engine(resolved_settings.database_url)


def create_async_engine_from_settings(
    settings: Settings | None = None,
) -> AsyncEngine:
    """Create an asynchronous engine using application settings."""
    resolved_settings = settings or get_settings()

    if resolved_settings.database_url is None:
        raise ConfigurationError(
            "Database URL is not configured. Set ATLAS_DATABASE_URL."
        )

    return create_async_database_engine(resolved_settings.database_url)


def create_session_factory(
    engine: Engine,
) -> sessionmaker[Session]:
    """Create a synchronous SQLAlchemy session factory."""
    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


def create_async_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create an asynchronous SQLAlchemy session factory."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session],
) -> Iterator[Session]:
    """Provide a synchronous session with rollback on failure.

    Transactions must be committed explicitly by the caller.
    """
    session = session_factory()

    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
