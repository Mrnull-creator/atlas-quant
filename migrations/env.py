"""Alembic migration environment for the Market Data Service."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from atlas_quant.platform.config import get_settings
from atlas_quant.platform.errors import ConfigurationError
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)
from atlas_quant.services.market_data.infrastructure.persistence.instrument import (
    InstrumentORM,  # noqa: F401 - registers the ORM model.
)
from atlas_quant.services.market_data.infrastructure.persistence.provider import (
    ProviderORM,  # noqa: F401 - registers the ORM model.
)


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


target_metadata = MarketDataBase.metadata


def get_database_url() -> str:
    """Retrieve the database URL without exposing it in application logs."""

    settings = get_settings()

    if settings.database_url is None:
        raise ConfigurationError(
            "Database URL is not configured. "
            "Set ATLAS_DATABASE_URL in the environment or .env file."
        )

    return settings.database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Run migrations without creating a database connection."""

    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using a live database connection."""

    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_database_url().replace(
        "%",
        "%%",
    )

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
