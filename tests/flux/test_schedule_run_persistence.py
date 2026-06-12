"""Regression tests: scheduler trigger run-state must be persisted.

``get_due_schedules`` returns detached ORM rows; the scheduler loop used to
call ``mark_run``/``mark_failure`` on those, so ``next_run_at`` never advanced
in the database and a due schedule re-fired on every poll while
``run_count``/``failure_count`` stayed at zero forever.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from flux.config import Configuration
from flux.domain.schedule import interval
from flux.schedule_manager import create_schedule_manager


@pytest.fixture
def manager(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'sched.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield create_schedule_manager()
    DatabaseRepository._engines.clear()


def _create(manager, name="s1"):
    return manager.create_schedule(
        workflow_id="wid",
        workflow_name="wf",
        workflow_namespace="default",
        name=name,
        schedule=interval(minutes=5),
    )


def _future():
    return datetime.now(timezone.utc) + timedelta(days=1)


def test_record_run_persists_and_schedule_stops_being_due(manager):
    sch = _create(manager)
    due = manager.get_due_schedules(current_time=_future())
    assert [d.id for d in due] == [sch.id]

    manager.record_run(sch.id, datetime.now(timezone.utc))

    fresh = manager.get_schedule(sch.id)
    assert fresh.run_count == 1
    assert fresh.last_run_at is not None
    assert fresh.next_run_at != sch.next_run_at, "next_run_at must advance in the DB"
    # The schedule must no longer be due for the same window it just ran in.
    assert manager.get_due_schedules(current_time=datetime.now(timezone.utc)) == []


def test_record_failure_persists(manager):
    sch = _create(manager)
    manager.record_failure(sch.id)
    manager.record_failure(sch.id)
    assert manager.get_schedule(sch.id).failure_count == 2


def test_record_on_deleted_schedule_does_not_raise(manager):
    sch = _create(manager)
    manager.delete_schedule(sch.id)
    manager.record_run(sch.id, datetime.now(timezone.utc))
    manager.record_failure(sch.id)


async def test_trigger_scheduled_workflow_persists_run(manager):
    """End-to-end through Server._trigger_scheduled_workflow (auth disabled)."""
    from flux.server import Server

    sch = _create(manager, name="trigger-test")
    due = manager.get_due_schedules(current_time=_future())
    assert len(due) == 1

    server = Server("127.0.0.1", 0)
    mock_ctx = MagicMock()
    mock_ctx.execution_id = "exec-sched-1"
    with patch.object(Server, "_create_execution", return_value=mock_ctx):
        await server._trigger_scheduled_workflow(due[0], datetime.now(timezone.utc))

    fresh = manager.get_schedule(sch.id)
    assert fresh.run_count == 1, "the trigger path must persist the run"
    assert manager.get_due_schedules(current_time=datetime.now(timezone.utc)) == [], (
        "a triggered schedule must not still be due (it would re-fire every poll)"
    )
