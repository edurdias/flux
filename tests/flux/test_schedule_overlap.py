"""Schedule overlap policy (issue #142).

``overlap_policy="skip"`` consumes a due fire while a previous execution of
the same schedule is still non-terminal; ``"allow"`` (and NULL, the value on
every pre-policy row) keeps the historic dispatch-regardless behavior. The
catch-up policy — a schedule due many intervals ago fires exactly once on
recovery — is also pinned here, since it was previously emergent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from flux.config import Configuration
from flux.domain.events import ExecutionState
from flux.domain.schedule import cron, interval, once, schedule_factory
from flux.schedule_manager import create_schedule_manager


@pytest.fixture
def manager(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'sched.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield create_schedule_manager()
    DatabaseRepository._engines.clear()


def _create(manager, name="s1", overlap="skip"):
    return manager.create_schedule(
        workflow_id="wid",
        workflow_name="wf",
        workflow_namespace="default",
        name=name,
        schedule=interval(minutes=5, overlap=overlap),
    )


def _add_execution(manager, schedule_id, state, execution_id="exec-1"):
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


class TestDomainOverlap:
    def test_default_is_skip_and_serializes(self):
        for schedule in (
            cron("* * * * *"),
            interval(minutes=1),
            once(datetime.now(timezone.utc) + timedelta(hours=1)),
        ):
            assert schedule.overlap == "skip"
            data = schedule.to_dict()
            assert data["overlap"] == "skip"
            assert schedule_factory(data).overlap == "skip"

    def test_allow_round_trips(self):
        schedule = cron("* * * * *", overlap="allow")
        assert schedule_factory(schedule.to_dict()).overlap == "allow"

    def test_invalid_policy_rejected(self):
        with pytest.raises(ValueError, match="overlap"):
            cron("* * * * *", overlap="queue")

    def test_pre_policy_instance_serializes_as_allow(self):
        """An instance unpickled from a pre-#142 row has no overlap attribute;
        to_dict must report the behavior it actually gets: allow."""
        schedule = cron("* * * * *")
        del schedule.overlap
        assert schedule.to_dict()["overlap"] == "allow"


class TestModelOverlap:
    def test_model_captures_policy_from_schedule(self, manager):
        skip = _create(manager, name="skip-one", overlap="skip")
        allow = _create(manager, name="allow-one", overlap="allow")
        assert manager.get_schedule(skip.id).overlap_policy == "skip"
        assert manager.get_schedule(allow.id).overlap_policy == "allow"

    def test_record_skip_advances_next_run_without_run_stats(self, manager):
        sch = _create(manager)
        before = manager.get_schedule(sch.id)

        manager.record_skip(sch.id, datetime.now(timezone.utc))

        after = manager.get_schedule(sch.id)
        assert after.skip_count == 1
        assert after.last_skipped_at is not None
        assert after.run_count == 0
        assert after.last_run_at is None
        assert after.next_run_at != before.next_run_at, "the fire must be consumed"
        # No longer due for the window it was skipped in.
        assert manager.get_due_schedules(current_time=datetime.now(timezone.utc)) == []

    def test_record_skip_on_deleted_schedule_does_not_raise(self, manager):
        sch = _create(manager)
        manager.delete_schedule(sch.id)
        manager.record_skip(sch.id, datetime.now(timezone.utc))


class TestOverlapDetection:
    def test_no_executions_means_no_overlap(self, manager):
        sch = _create(manager)
        assert manager.has_active_execution(sch.id) is False

    @pytest.mark.parametrize(
        "state",
        [
            ExecutionState.SCHEDULED,
            ExecutionState.CLAIMED,
            ExecutionState.RUNNING,
            ExecutionState.PAUSED,
        ],
    )
    def test_non_terminal_execution_is_overlap(self, manager, state):
        sch = _create(manager)
        _add_execution(manager, sch.id, state)
        assert manager.has_active_execution(sch.id) is True

    @pytest.mark.parametrize(
        "state",
        [ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED],
    )
    def test_terminal_execution_is_not_overlap(self, manager, state):
        sch = _create(manager)
        _add_execution(manager, sch.id, state)
        assert manager.has_active_execution(sch.id) is False

    def test_other_schedules_executions_do_not_count(self, manager):
        sch = _create(manager, name="mine")
        other = _create(manager, name="other")
        _add_execution(manager, other.id, ExecutionState.RUNNING)
        assert manager.has_active_execution(sch.id) is False
        assert manager.has_active_execution(other.id) is True


class TestCatchUpPolicy:
    def test_long_overdue_schedule_fires_exactly_once(self, manager):
        """Run-once catch-up: next_run_at hours in the past yields one due
        fire; record_run advances from the run time, not per missed
        interval."""
        sch = _create(manager)
        past = datetime.now(timezone.utc) - timedelta(hours=6)
        with manager._repository.session() as session:
            from flux.models import ScheduleModel

            row = session.query(ScheduleModel).filter(ScheduleModel.id == sch.id).one()
            row.next_run_at = past
            session.commit()

        now = datetime.now(timezone.utc)
        due = manager.get_due_schedules(current_time=now)
        assert [d.id for d in due] == [sch.id]

        manager.record_run(sch.id, now)

        fresh = manager.get_schedule(sch.id)
        # One catch-up fire consumed the entire backlog: the next run is in
        # the future relative to the recovery, not 71 more overdue intervals.
        next_run = fresh.next_run_at
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        assert next_run > now
        assert manager.get_due_schedules(current_time=now) == []
        assert fresh.run_count == 1
