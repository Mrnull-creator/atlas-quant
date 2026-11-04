"""Shared SQLAlchemy engine and session management."""

from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from atlas_quant.platform.config import Settings, get_settings
from atlas_quant.platform.errors import ConfigurationError


def create_database_engine(
    database_url: SecretStr | str,
    *,
    echo: bool = False,
) -> Engine:
    """Create a SQLAlchemy engine without exposing database credentials."""

    url = (
        database_url.get_secret_value()
        if isinstance(database_url, SecretStr)
        else database_url
    )

    if not isinstance(url, str) or not url.strip():
        raise ConfigurationError("Database URL must be configured.")

    return create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
    )


def create_engine_from_settings(
    settings: Settings | None = None,
) -> Engine:
    """Create the database engine using centralized application settings."""

    resolved_settings = settings or get_settings()

    if resolved_settings.database_url is None:
        raise ConfigurationError(
            "Database URL is not configured. "
            "Set ATLAS_DATABASE_URL in the environment."
        )

    return create_database_engine(resolved_settings.database_url)


def create_session_factory(
    engine: Engine,
) -> sessionmaker[Session]:
    """Create a reusable SQLAlchemy session factory."""

    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session],
) -> Iterator[Session]:
    """Provide a session with reliable rollback and cleanup.

    Transactions are committed explicitly by the caller.
    Exceptions trigger rollback before being propagated.
    """

    session = session_factory()

    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
