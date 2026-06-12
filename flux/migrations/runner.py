"""Programmatic Alembic migration runner.

Replaces the historical ``Base.metadata.create_all`` call. On every repository
construction this brings the database schema to ``head`` with three cases:

* **fresh database** (no tables): the full migration chain runs, creating the
  current schema.
* **Alembic-managed database** (``alembic_version`` table present): upgraded to
  ``head``.
* **legacy database** (tables exist but no ``alembic_version`` — created by the
  old ``create_all`` path): stamped at the baseline revision, then upgraded, so
  pre-Alembic deployments migrate forward in place instead of being dropped and
  recreated.

Concurrency is guarded by a PostgreSQL transaction-less advisory lock so two
server replicas (or a server + worker) starting at once cannot run migrations
simultaneously. SQLite is single-writer/single-node, so the lock is a no-op
there.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from flux.utils import get_logger

logger = get_logger(__name__)

# A stable 64-bit key for pg_advisory_lock, derived once. Arbitrary but fixed.
_MIGRATION_LOCK_KEY = 0x464C5558  # "FLUX"

_MIGRATIONS_DIR = Path(__file__).resolve().parent
# Revision the legacy (pre-Alembic) ``create_all`` schema is stamped at.
_BASELINE_REVISION = "0001_baseline"


def _alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    # Hand the live engine to env.py so it binds to the same connection pool
    # instead of opening a second one.
    cfg.attributes["connection_engine"] = engine
    return cfg


def _is_legacy_schema(connection) -> bool:
    """A non-empty database that Alembic has never managed."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if not tables:
        return False
    return "alembic_version" not in tables


def run_migrations(engine: Engine) -> None:
    """Bring ``engine``'s database to the latest schema revision."""
    cfg = _alembic_config(engine)
    is_postgres = engine.dialect.name == "postgresql"

    with engine.connect() as connection:
        if is_postgres:
            connection.exec_driver_sql(
                "SELECT pg_advisory_lock(%(key)s)",
                {"key": _MIGRATION_LOCK_KEY},
            )
        # Pin Alembic to *this* connection so the migration runs in the same
        # session that holds the advisory lock (env.py reuses it) rather than
        # opening a second, unguarded connection.
        cfg.attributes["connection"] = connection
        try:
            legacy = _is_legacy_schema(connection)
            if legacy:
                head = ScriptDirectory.from_config(cfg).get_current_head()
                logger.info(
                    "Existing pre-Alembic database detected; stamping baseline "
                    f"'{_BASELINE_REVISION}' and upgrading to '{head}'.",
                )
                command.stamp(cfg, _BASELINE_REVISION)

            current = MigrationContext.configure(connection).get_current_revision()
            command.upgrade(cfg, "head")
            new = MigrationContext.configure(connection).get_current_revision()
            if current != new:
                logger.info(f"Database schema migrated: {current or 'empty'} -> {new}")
        finally:
            if is_postgres:
                connection.exec_driver_sql(
                    "SELECT pg_advisory_unlock(%(key)s)",
                    {"key": _MIGRATION_LOCK_KEY},
                )
            connection.commit()


def current_revision(engine: Engine) -> str | None:
    """Return the database's current Alembic revision (or None if unmanaged)."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()
