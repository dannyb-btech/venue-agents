"""Alembic environment for the catalog: migrations run as the catalog owner (CATALOG_ADMIN_URL).

Tests and tools can pass an open connection in `config.attributes["connection"]` instead.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from va_core.errors import ConfigurationError
from va_db.catalog.models import CatalogBase
from va_db.settings import MigrationSettings

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = CatalogBase.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if isinstance(connection, Connection):
        _run(connection)
        return
    url = MigrationSettings.load().catalog_admin_url
    if url is None:
        raise ConfigurationError("CATALOG_ADMIN_URL is not set")
    engine = create_engine(url.get_secret_value(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


if context.is_offline_mode():
    raise ConfigurationError("offline (--sql) migrations are not supported")
run_migrations_online()
