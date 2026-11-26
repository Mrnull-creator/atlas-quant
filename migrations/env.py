"""Alembic migration environment for Atlas Quant services."""

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
from atlas_quant.services.market_data.infrastructure.persistence.provider_instrument_mapping import (
    ProviderInstrumentMappingORM,  # noqa: F401 - registers the ORM model.
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset import (
    DatasetORM,  # noqa: F401 - registers the ORM model.
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_version import (
    DatasetVersionORM,  # noqa: F401 - registers the ORM model.
)
from atlas_quant.services.market_data.infrastructure.persistence.ingestion_job import (
    IngestionJobDatasetVersionORM,  # noqa: F401 - registers the ORM model.
    IngestionJobORM,  # noqa: F401 - registers the ORM model.
)
from atlas_quant.services.market_data.infrastructure.persistence.dataset_lineage import (
    DatasetVersionLineageORM,  # noqa: F401 - registers the ORM model.
)

from atlas_quant.services.execution.infrastructure.persistence import (
    ExecutionBase,
    PaperAccountORM,  # noqa: F401 - registers the ORM model.
    OrderORM,  # noqa: F401 - registers the ORM model.
    FillORM,  # noqa: F401 - registers the ORM model.
    CashLedgerEntryORM,  # noqa: F401 - registers the ORM model.
    PositionORM,  # noqa: F401 - registers the ORM model.
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# Each service owns its metadata. Alembic manages both in one migration chain.
target_metadata = [
    MarketDataBase.metadata,
    ExecutionBase.metadata,
]


def include_name(name, type_, parent_names):
    """Restrict schema inspection to schemas owned by Atlas Quant."""

    if type_ == "schema":
        # Alembic represents the connection's default schema as None.
        return name in (None, "execution")

    return True


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
        include_schemas=True,
        include_name=include_name,
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
                include_schemas=True,
                include_name=include_name,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()