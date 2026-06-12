"""Tests for the Alembic migration runner and its three startup paths."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect

from flux.migrations.runner import current_revision, run_migrations
from flux.models import Base

HEAD = "0002_backfill_indexes"
BASELINE = "0001_baseline"

# A representative index added after the original create_all schema, used to
# prove the legacy backfill path actually repairs old databases.
_BACKFILL_TABLE = "executions"
_BACKFILL_INDEX = "ix_executions_workflow_id"


def _engine(tmp_path, name="m.db"):
    return create_engine(f"sqlite:///{tmp_path / name}")


def test_fresh_database_migrates_to_head(tmp_path):
    engine = _engine(tmp_path)
    run_migrations(engine)

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert "alembic_version" in tables
    assert "executions" in tables and "workflows" in tables
    assert current_revision(engine) == HEAD
    # Indexes are present on a fresh DB straight from the baseline.
    assert _BACKFILL_INDEX in {ix["name"] for ix in insp.get_indexes(_BACKFILL_TABLE)}


def test_legacy_database_is_stamped_and_backfilled(tmp_path):
    engine = _engine(tmp_path, "legacy.db")
    # Simulate the pre-Alembic create_all schema, then drop an index to emulate
    # a database created before that index existed.
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {_BACKFILL_INDEX}")

    insp = inspect(engine)
    assert "alembic_version" not in set(insp.get_table_names())
    assert _BACKFILL_INDEX not in {ix["name"] for ix in insp.get_indexes(_BACKFILL_TABLE)}

    run_migrations(engine)

    insp = inspect(engine)
    assert current_revision(engine) == HEAD
    assert _BACKFILL_INDEX in {ix["name"] for ix in insp.get_indexes(_BACKFILL_TABLE)}


def test_migration_is_idempotent(tmp_path):
    engine = _engine(tmp_path, "idem.db")
    run_migrations(engine)
    rev = current_revision(engine)
    # Re-running must not error or change the revision.
    run_migrations(engine)
    run_migrations(engine)
    assert current_revision(engine) == rev == HEAD


def test_baseline_creates_same_tables_as_metadata(tmp_path):
    """The baseline (fresh path) schema must match the ORM metadata exactly."""
    engine = _engine(tmp_path, "schema.db")
    run_migrations(engine)
    actual = set(inspect(engine).get_table_names()) - {"alembic_version"}
    expected = set(Base.metadata.tables.keys())
    assert actual == expected


@pytest.mark.parametrize("missing", ["idx_execution_state", "idx_schedule_status_next_run"])
def test_backfill_restores_specific_indexes(tmp_path, missing):
    engine = _engine(tmp_path, f"{missing}.db")
    Base.metadata.create_all(engine)
    table = "executions" if missing.startswith("idx_execution") else "schedules"
    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {missing}")
    run_migrations(engine)
    assert missing in {ix["name"] for ix in inspect(engine).get_indexes(table)}
