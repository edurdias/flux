"""PostgreSQL migration tests — the production-critical legacy-upgrade path.

These exercise behavior that SQLite cannot: the ``pg_advisory_lock`` guard,
native ENUM types, and ALTER-free index creation on a populated database. They
run only when ``FLUX_DATABASE_URL`` points at PostgreSQL (i.e. under
``make test-postgresql`` / the PostgreSQL CI lane) and are skipped otherwise.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from flux.migrations.runner import current_revision, run_migrations
from flux.models import Base, normalize_postgresql_url

_DB_URL = os.environ.get("FLUX_DATABASE_URL", "")
_ENGINE_URL = normalize_postgresql_url(_DB_URL)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        not _DB_URL.startswith("postgresql://"),
        reason="requires a PostgreSQL FLUX_DATABASE_URL",
    ),
]

HEAD = "0007_worker_runners"
_BACKFILL_INDEX = "ix_executions_workflow_id"


def _fresh_schema_engine():
    """An engine pointed at a brand-new, empty PostgreSQL schema (search_path)."""
    schema = f"mig_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_ENGINE_URL)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(_ENGINE_URL, connect_args={"options": f"-csearch_path={schema}"})
    return engine, schema, admin


def _drop_schema(admin, schema):
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_pg_fresh_database_migrates_to_head():
    engine, schema, admin = _fresh_schema_engine()
    try:
        run_migrations(engine)
        assert current_revision(engine) == HEAD
        insp = inspect(engine)
        assert "executions" in insp.get_table_names()
        assert _BACKFILL_INDEX in {ix["name"] for ix in insp.get_indexes("executions")}
    finally:
        engine.dispose()
        _drop_schema(admin, schema)
        admin.dispose()


def test_pg_legacy_database_is_stamped_and_backfilled():
    engine, schema, admin = _fresh_schema_engine()
    try:
        # Emulate the pre-Alembic create_all schema, then drop an index to
        # represent a database created before that index existed.
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text(f"DROP INDEX IF EXISTS {_BACKFILL_INDEX}"))

        insp = inspect(engine)
        assert "alembic_version" not in set(insp.get_table_names())
        assert _BACKFILL_INDEX not in {ix["name"] for ix in insp.get_indexes("executions")}

        run_migrations(engine)

        insp = inspect(engine)
        assert current_revision(engine) == HEAD
        assert _BACKFILL_INDEX in {ix["name"] for ix in insp.get_indexes("executions")}
    finally:
        engine.dispose()
        _drop_schema(admin, schema)
        admin.dispose()


def test_pg_migration_is_idempotent():
    engine, schema, admin = _fresh_schema_engine()
    try:
        run_migrations(engine)
        run_migrations(engine)
        assert current_revision(engine) == HEAD
    finally:
        engine.dispose()
        _drop_schema(admin, schema)
        admin.dispose()
