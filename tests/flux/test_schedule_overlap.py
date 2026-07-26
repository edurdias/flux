"""Overlap policy: a due fire must not stack on a still-running execution.

The dispatch cycle used to be trigger-and-forget, so a workflow slower than its
own interval accumulated concurrent executions without bound -- every worker
slot filling with copies of one schedule while unrelated work starved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from flux.config import Configuration
from flux.domain.events import ExecutionState
from flux.domain.schedule import interval
from flux.schedule_manager import create_schedule_manager


@pytest.fixture
def manager(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'overlap.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield create_schedule_manager()
    DatabaseRepository._engines.clear()


def _create(manager, name="s1", **kwargs):
    return manager.create_schedule(
        workflow_id="wid",
        workflow_name="wf",
        workflow_namespace="default",
        name=name,
        schedule=kwargs.pop("schedule", interval(minutes=5)),
        **kwargs,
    )


def _add_execution(manager, schedule_id, execution_id, state):
    from flux.models import ExecutionContextModel

    with manager._repository.session() as session:
        session.add(
            ExecutionContextModel(
                execution_id=execution_id,
                workflow_id="wid",
                workflow_name="wf",
                input=None,
                state=state,
                schedule_id=schedule_id,
            ),
        )
        session.commit()


def _future():
    return datetime.now(timezone.utc) + timedelta(days=1)


def _make_due_now(manager, schedule_id):
    """Backdate next_run_at so the real scheduler loop, which uses wall-clock
    ``datetime.now()``, sees this schedule as due in the cycle under test."""
    from flux.models import ScheduleModel

    with manager._repository.session() as session:
        row = session.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
        row.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()


# -- policy resolution ---------------------------------------------------------


def test_new_schedules_default_to_skip(manager):
    assert _create(manager).effective_overlap_policy == "skip"


def test_policy_comes_off_the_schedule_object(manager):
    sch = _create(manager, schedule=interval(minutes=5, overlap="allow"))
    assert manager.get_schedule(sch.id).effective_overlap_policy == "allow"


def test_explicit_argument_wins_over_the_schedule_object(manager):
    sch = _create(manager, schedule=interval(minutes=5, overlap="skip"), overlap_policy="allow")
    assert manager.get_schedule(sch.id).effective_overlap_policy == "allow"


def test_null_policy_reads_as_allow(manager):
    """Rows written before this column existed keep their old behavior."""
    from flux.models import ScheduleModel

    sch = _create(manager)
    with manager._repository.session() as session:
        row = session.query(ScheduleModel).filter(ScheduleModel.id == sch.id).first()
        row.overlap_policy = None  # simulate a pre-migration row
        session.commit()

    assert manager.get_schedule(sch.id).effective_overlap_policy == "allow"


# -- the overlap query ---------------------------------------------------------


def test_no_executions_means_no_overlap(manager):
    assert manager.has_active_execution(_create(manager).id) is False


@pytest.mark.parametrize(
    "state",
    [
        ExecutionState.CREATED,
        ExecutionState.SCHEDULED,
        ExecutionState.CLAIMED,
        ExecutionState.RUNNING,
        ExecutionState.PAUSED,
        ExecutionState.CANCELLING,
    ],
)
def test_non_terminal_execution_is_an_overlap(manager, state):
    sch = _create(manager)
    _add_execution(manager, sch.id, f"exec-{state.value}", state)
    assert manager.has_active_execution(sch.id) is True


@pytest.mark.parametrize(
    "state",
    [ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED],
)
def test_terminal_execution_is_not_an_overlap(manager, state):
    sch = _create(manager)
    _add_execution(manager, sch.id, f"exec-{state.value}", state)
    assert manager.has_active_execution(sch.id) is False


def test_another_schedules_execution_is_not_an_overlap(manager):
    mine = _create(manager, name="mine")
    theirs = _create(manager, name="theirs")
    _add_execution(manager, theirs.id, "exec-theirs", ExecutionState.RUNNING)
    assert manager.has_active_execution(mine.id) is False


# -- recording a skip ----------------------------------------------------------


def test_record_skip_counts_and_advances_without_marking_a_run(manager):
    sch = _create(manager)
    before = sch.next_run_at

    manager.record_skip(sch.id, datetime.now(timezone.utc))

    fresh = manager.get_schedule(sch.id)
    assert fresh.skipped_count == 1
    assert fresh.last_skipped_at is not None
    assert fresh.next_run_at != before, "a skipped occurrence must still advance"
    # Nothing ran, so the run stats must be untouched.
    assert fresh.run_count == 0
    assert fresh.last_run_at is None


def test_skipped_schedule_stops_being_due(manager):
    """Without the advance the schedule stays due and is re-checked every poll."""
    sch = _create(manager)
    assert [d.id for d in manager.get_due_schedules(current_time=_future())] == [sch.id]

    manager.record_skip(sch.id, datetime.now(timezone.utc))

    assert manager.get_due_schedules(current_time=datetime.now(timezone.utc)) == []


def test_record_skip_on_deleted_schedule_does_not_raise(manager):
    sch = _create(manager)
    manager.delete_schedule(sch.id)
    manager.record_skip(sch.id, datetime.now(timezone.utc))


# -- the dispatch decision -----------------------------------------------------


async def _run_one_scheduler_cycle(server):
    """Drive the real ``_scheduler_loop`` through exactly one iteration.

    The loop sleeps *before* doing its work, so clearing the run flag from the
    patched sleep lets this cycle complete and then exits. Testing the real loop
    matters: a test that re-implements the guard would pass even if the guard
    were missing from the dispatch path.
    """
    server.scheduler_running = True
    server.poll_interval = 0

    async def _stop_after_this_cycle(_seconds):
        server.scheduler_running = False

    with patch("flux.server.asyncio.sleep", _stop_after_this_cycle):
        await server._scheduler_loop()


async def test_dispatch_skips_when_a_previous_execution_is_still_running(manager):
    """The guard fires inside the real loop: no execution created, skip recorded."""
    from flux.server import Server

    sch = _create(manager, name="busy")
    _add_execution(manager, sch.id, "exec-inflight", ExecutionState.RUNNING)
    _make_due_now(manager, sch.id)

    server = Server("127.0.0.1", 0)
    with patch.object(Server, "_create_execution") as create:
        await _run_one_scheduler_cycle(server)
        # The guard must short-circuit before the trigger path builds anything.
        create.assert_not_called()

    fresh = manager.get_schedule(sch.id)
    assert fresh.skipped_count == 1
    assert fresh.run_count == 0
    assert fresh.last_run_at is None


async def test_allow_policy_still_dispatches_over_a_running_execution(manager):
    """Back-compat: the historical behavior is preserved for allow."""
    from flux.server import Server

    sch = _create(manager, name="stacker", schedule=interval(minutes=5, overlap="allow"))
    _add_execution(manager, sch.id, "exec-inflight", ExecutionState.RUNNING)
    _make_due_now(manager, sch.id)

    server = Server("127.0.0.1", 0)
    mock_ctx = MagicMock()
    mock_ctx.execution_id = "exec-second"
    with patch.object(Server, "_create_execution", return_value=mock_ctx) as create:
        await _run_one_scheduler_cycle(server)
        create.assert_called_once()

    fresh = manager.get_schedule(sch.id)
    assert fresh.run_count == 1, "allow must still dispatch over a running execution"
    assert fresh.skipped_count == 0
