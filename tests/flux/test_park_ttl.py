"""Park TTL for unclaimed executions (issue #157).

An execution whose constraints match no current worker parks (state
CREATED) indefinitely — right for elastic batch fleets, indistinguishable
from a hang for an interactive caller. Executions that opt into a bound
(per-run ``park_ttl`` or the ``[flux.workers] park_ttl`` default) are
failed terminally by the scheduler sweep once the deadline passes, with a
diagnosable ``ParkTimeoutError``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from flux import ExecutionContext
from flux.config import Configuration
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEventType, ExecutionState


@pytest.fixture
def manager(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'park.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield ContextManager.create()
    DatabaseRepository._engines.clear()


def _register_workflow(manager, workflow_id="wf-1", affinity=None):
    from flux.models import WorkflowModel

    with manager.session() as session:
        session.add(
            WorkflowModel(
                id=workflow_id,
                name="parked_wf",
                version=1,
                imports=[],
                source=b"",
                affinity=affinity,
            ),
        )
        session.commit()


def _save_execution(manager, park_ttl=None, workflow_id="wf-1"):
    ctx = ExecutionContext(
        workflow_id=workflow_id,
        workflow_namespace="default",
        workflow_name="parked_wf",
        input=None,
    )
    manager.save(ctx, park_ttl=park_ttl)
    return ctx


def _row(manager, execution_id):
    from flux.models import ExecutionContextModel

    with manager.session() as session:
        return session.get(ExecutionContextModel, execution_id)


def _set_deadline(manager, execution_id, deadline):
    from flux.models import ExecutionContextModel

    with manager.session() as session:
        row = session.get(ExecutionContextModel, execution_id)
        row.park_deadline = deadline
        session.commit()


class TestDeadlineAtSubmission:
    def test_default_config_zero_means_no_deadline(self, manager):
        _register_workflow(manager)
        ctx = _save_execution(manager)
        assert _row(manager, ctx.execution_id).park_deadline is None

    def test_per_run_ttl_sets_deadline(self, manager):
        _register_workflow(manager)
        ctx = _save_execution(manager, park_ttl=60)
        deadline = _row(manager, ctx.execution_id).park_deadline
        assert deadline is not None

    def test_config_default_applies_when_no_override(self, manager):
        _register_workflow(manager)
        Configuration.get().override(workers={"park_ttl": 120})
        try:
            ctx = _save_execution(manager)
            assert _row(manager, ctx.execution_id).park_deadline is not None
        finally:
            Configuration.get().override(workers={"park_ttl": 0})

    def test_explicit_zero_override_disables_config_default(self, manager):
        _register_workflow(manager)
        Configuration.get().override(workers={"park_ttl": 120})
        try:
            ctx = _save_execution(manager, park_ttl=0)
            assert _row(manager, ctx.execution_id).park_deadline is None
        finally:
            Configuration.get().override(workers={"park_ttl": 0})


class TestSweep:
    def test_expired_parked_execution_fails_with_diagnostic(self, manager):
        _register_workflow(manager, affinity={"gpu": "true"})
        ctx = _save_execution(manager, park_ttl=60)
        _set_deadline(
            manager,
            ctx.execution_id,
            datetime.now(timezone.utc) - timedelta(seconds=5),
        )

        failed = manager.fail_expired_parked()

        assert failed == [ctx.execution_id]
        stored = manager.get(ctx.execution_id)
        assert stored.state == ExecutionState.FAILED
        failure_events = [e for e in stored.events if e.type == ExecutionEventType.WORKFLOW_FAILED]
        assert len(failure_events) == 1
        value = failure_events[0].value
        assert value["type"] == "ParkTimeoutError"
        assert "park deadline" in value["message"]
        assert "gpu" in value["message"], "the unmatched constraint must be named"

    def test_unexpired_execution_is_untouched(self, manager):
        _register_workflow(manager)
        ctx = _save_execution(manager, park_ttl=3600)
        assert manager.fail_expired_parked() == []
        assert _row(manager, ctx.execution_id).state == ExecutionState.CREATED

    def test_execution_without_deadline_is_never_swept(self, manager):
        _register_workflow(manager)
        ctx = _save_execution(manager)  # parks forever, the historic default
        assert manager.fail_expired_parked() == []
        assert _row(manager, ctx.execution_id).state == ExecutionState.CREATED

    def test_claimed_execution_is_not_swept_even_past_deadline(self, manager):
        """The TTL bounds *parking*; once claimed, the execution belongs to
        the worker and the ordinary timeouts apply."""
        from flux.models import ExecutionContextModel

        _register_workflow(manager)
        ctx = _save_execution(manager, park_ttl=60)
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        with manager.session() as session:
            row = session.get(ExecutionContextModel, ctx.execution_id)
            row.park_deadline = past
            row.state = ExecutionState.RUNNING
            session.commit()

        assert manager.fail_expired_parked() == []
        assert _row(manager, ctx.execution_id).state == ExecutionState.RUNNING

    def test_sweep_is_idempotent(self, manager):
        _register_workflow(manager)
        ctx = _save_execution(manager, park_ttl=60)
        _set_deadline(
            manager,
            ctx.execution_id,
            datetime.now(timezone.utc) - timedelta(seconds=5),
        )
        assert manager.fail_expired_parked() == [ctx.execution_id]
        assert manager.fail_expired_parked() == []
