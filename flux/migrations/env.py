"""Alembic environment for Flux.

Driven programmatically by ``flux.migrations.runner`` (and the ``flux db`` CLI),
not by a standalone ``alembic.ini``. When invoked through the runner the live
SQLAlchemy engine is passed via ``config.attributes['connection_engine']``;
when invoked from the CLI a URL is supplied instead.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

# The security models live outside flux.models and register on Base only when
# imported; without this, target_metadata misses roles/api_keys/principals/
# principal_roles and autogenerate would propose dropping them.
import flux.security.models  # noqa: F401
from flux.models import Base

config = context.config
target_metadata = Base.metadata


def _get_engine():
    engine = config.attributes.get("connection_engine", None)
    if engine is not None:
        return engine
    return engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _configure_and_run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # batch mode lets SQLite emulate ALTER TABLE operations.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # The runner pins a connection (the one holding the PostgreSQL advisory
    # lock); reuse it so migrations run in that same session rather than opening
    # a second, unguarded connection.
    connection = config.attributes.get("connection", None)
    if connection is not None:
        _configure_and_run(connection)
        return

    engine = _get_engine()
    with engine.connect() as connection:
        _configure_and_run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
