"""PostgreSQL migration tests — the production-critical legacy-upgrade path.

These exercise behavior that SQLite cannot: the ``pg_advisory_lock`` guard,
native ENUM types, and ALTER-free index creation on a populated database. They
run only when ``FLUX_DATABASE_URL`` points at PostgreSQL (i.e. under
``make test-postgresql`` / the PostgreSQL CI lane) and are skipped otherwise.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from datetime import timezone

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

HEAD = "0028_agent_hooks"
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


def test_pg_event_time_is_timestamptz_and_reinterprets_legacy_rows_as_utc():
    """0014 converts execution_events.time to timestamptz.

    PostgreSQL hands back a naive datetime from a plain ``timestamp`` column,
    which cannot be compared against the aware stamps events now carry (#169).
    The conversion must pin existing naive values to UTC explicitly — without a
    USING clause PostgreSQL reinterprets them through the session TimeZone,
    which varies by deployment.
    """
    engine, schema, admin = _fresh_schema_engine()
    try:
        Base.metadata.create_all(engine)
        # Emulate a pre-0014 schema holding a naive timestamp.
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE execution_events ALTER COLUMN time TYPE timestamp"),
            )
            # PostgreSQL enforces the workflows/executions FKs SQLite ignores.
            conn.execute(
                text(
                    "INSERT INTO workflows (id, namespace, name, version, source) "
                    "VALUES ('wf-1', 'default', 'wf', 1, '')",
                ),
            )
            conn.execute(
                text(
                    "INSERT INTO executions (execution_id, workflow_id, "
                    "workflow_namespace, workflow_name, state, claim_generation) "
                    "VALUES ('exec-1', 'wf-1', 'default', 'wf', 'COMPLETED', 0)",
                ),
            )
            conn.execute(
                text(
                    "INSERT INTO execution_events "
                    "(execution_id, source_id, event_id, type, name, time) "
                    "VALUES ('exec-1', 'src', 'abc123', 'WORKFLOW_STARTED', 'wf', "
                    "'2026-08-07 00:57:11')",
                ),
            )

        run_migrations(engine)

        assert current_revision(engine) == HEAD
        col = next(
            c for c in inspect(engine).get_columns("execution_events") if c["name"] == "time"
        )
        assert col["type"].timezone is True, f"expected timestamptz, got {col['type']}"

        # The pre-existing naive value is pinned to UTC, not to the session zone.
        with engine.begin() as conn:
            conn.execute(text("SET TIME ZONE 'America/New_York'"))
            stored = conn.execute(
                text("SELECT time FROM execution_events WHERE event_id = 'abc123'"),
            ).scalar_one()
        assert stored == datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)
    finally:
        engine.dispose()
        _drop_schema(admin, schema)
        admin.dispose()
