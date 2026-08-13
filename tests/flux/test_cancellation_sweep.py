"""Orphaned-cancellation sweep (issue #225).

Cancellation delivery matches ``worker_name`` against connected workers, so a
row cancelled while parked (no worker) or assigned to a worker that never
returns stays CANCELLING forever. The scheduler sweep resolves both; rows
whose worker is connected stay owned by re-delivery (issue #189 / PR #222).
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
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'sweep.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield ContextManager.create()
    DatabaseRepository._engines.clear()


def _register_workflow(manager, workflow_id="wf-1"):
    from flux.models import WorkflowModel

    with manager.session() as session:
        session.add(
            WorkflowModel(
                id=workflow_id,
                name="cancellable_wf",
                version=1,
                imports=[],
                source=b"",
            ),
        )
        session.commit()


def _cancelling_execution(manager, worker_name=None, cancelling_age_seconds=0):
    """A CANCELLING row as the cancel route leaves it, optionally assigned
    to a worker and with its WORKFLOW_CANCELLING event backdated."""
    from flux.models import ExecutionContextModel, ExecutionEventModel

    ctx = ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="default",
        workflow_name="cancellable_wf",
        input=None,
    )
    manager.save(ctx)
    ctx.start_cancel()
    manager.save(ctx)
    with manager.session() as session:
        row = session.get(ExecutionContextModel, ctx.execution_id)
        row.worker_name = worker_name
        if cancelling_age_seconds:
            stamp = datetime.now(timezone.utc) - timedelta(seconds=cancelling_age_seconds)
            for event in (
                session.query(ExecutionEventModel)
                .filter(
                    ExecutionEventModel.execution_id == ctx.execution_id,
                    ExecutionEventModel.type == ExecutionEventType.WORKFLOW_CANCELLING,
                )
                .all()
            ):
                event.time = stamp
        session.commit()
    return ctx


def _state(manager, execution_id) -> ExecutionState:
    return manager.get(execution_id).state


class TestNullOwner:
    def test_resolves_immediately(self, manager):
        """Nothing was ever asked to resolve it, so there is nobody to wait
        for — cancelled-while-parked rows resolve on the first sweep."""
        _register_workflow(manager)
        ctx = _cancelling_execution(manager, worker_name=None)

        resolved = manager.resolve_orphaned_cancellations(["some-worker"], 300)

        assert resolved == [ctx.execution_id]
        assert _state(manager, ctx.execution_id) == ExecutionState.CANCELLED

    def test_resolution_appends_the_cancelled_event(self, manager):
        _register_workflow(manager)
        ctx = _cancelling_execution(manager, worker_name=None)

        manager.resolve_orphaned_cancellations([], 300)

        events = [e.type for e in manager.get(ctx.execution_id).events]
        assert ExecutionEventType.WORKFLOW_CANCELLED in events

    def test_resolves_even_with_grace_disabled(self, manager):
        """Grace 0 means 'wait forever for named workers' — it must not
        also strand rows that have no worker to wait for."""
        _register_workflow(manager)
        ctx = _cancelling_execution(manager, worker_name=None)

        resolved = manager.resolve_orphaned_cancellations([], 0)

        assert resolved == [ctx.execution_id]


class TestNamedOwner:
    def test_connected_worker_is_left_alone(self, manager):
        """Re-delivery owns rows whose worker is connected, no matter how
        stale — the sweep must not race the worker's own resolution."""
        _register_workflow(manager)
        ctx = _cancelling_execution(manager, worker_name="w1", cancelling_age_seconds=3600)

        resolved = manager.resolve_orphaned_cancellations(["w1"], 300)

        assert resolved == []
        assert _state(manager, ctx.execution_id) == ExecutionState.CANCELLING

    def test_disconnected_inside_grace_waits(self, manager):
        """A worker in reconnect backoff still gets its chance: its own
        resolution interrupts the running body, the sweep's abandons it."""
        _register_workflow(manager)
        ctx = _cancelling_execution(manager, worker_name="w1", cancelling_age_seconds=10)

        resolved = manager.resolve_orphaned_cancellations([], 300)

        assert resolved == []
        assert _state(manager, ctx.execution_id) == ExecutionState.CANCELLING

    def test_disconnected_past_grace_resolves(self, manager):
        _register_workflow(manager)
        ctx = _cancelling_execution(manager, worker_name="w1", cancelling_age_seconds=3600)

        resolved = manager.resolve_orphaned_cancellations([], 300)

        assert resolved == [ctx.execution_id]
        assert _state(manager, ctx.execution_id) == ExecutionState.CANCELLED

    def test_worker_heartbeating_through_another_replica_is_left_alone(self, manager):
        """connected_workers is the sweeping replica's SSE view, per-replica
        by construction. A worker connected to a different replica still
        heartbeats into workers.last_seen_at, and that must protect its rows
        — otherwise a multi-replica deployment resolves cancellations for
        workers that are alive and mid-run."""
        from flux.models import WorkerModel

        _register_workflow(manager)
        ctx = _cancelling_execution(manager, worker_name="w1", cancelling_age_seconds=3600)
        with manager.session() as session:
            live = WorkerModel(name="w1")
            live.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.add(live)
            session.commit()

        resolved = manager.resolve_orphaned_cancellations([], 300, liveness_seconds=60)

        assert resolved == []
        assert _state(manager, ctx.execution_id) == ExecutionState.CANCELLING

    def test_worker_with_a_stale_heartbeat_is_swept(self, manager):
        from flux.models import WorkerModel

        _register_workflow(manager)
        ctx = _cancelling_execution(manager, worker_name="w1", cancelling_age_seconds=3600)
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=600)
        with manager.session() as session:
            gone = WorkerModel(name="w1")
            gone.last_seen_at = stale
            session.add(gone)
            session.commit()

        resolved = manager.resolve_orphaned_cancellations([], 300, liveness_seconds=60)

        assert resolved == [ctx.execution_id]
        assert _state(manager, ctx.execution_id) == ExecutionState.CANCELLED

    def test_grace_zero_waits_forever(self, manager):
        _register_workflow(manager)
        ctx = _cancelling_execution(manager, worker_name="w1", cancelling_age_seconds=3600)

        resolved = manager.resolve_orphaned_cancellations([], 0)

        assert resolved == []
        assert _state(manager, ctx.execution_id) == ExecutionState.CANCELLING


class TestNonCancellingRows:
    def test_terminal_and_active_rows_untouched(self, manager):
        _register_workflow(manager)
        ctx = ExecutionContext(
            workflow_id="wf-1",
            workflow_namespace="default",
            workflow_name="cancellable_wf",
            input=None,
        )
        manager.save(ctx)

        resolved = manager.resolve_orphaned_cancellations([], 300)

        assert resolved == []
        assert _state(manager, ctx.execution_id) == ExecutionState.CREATED
