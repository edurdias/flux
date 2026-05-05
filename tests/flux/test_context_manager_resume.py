"""Tests for the resume-claim additions in DatabaseContextManager."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from flux.context_managers import DatabaseContextManager
from flux.domain.events import ExecutionEvent, ExecutionEventType, ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.errors import ExecutionError
from flux.models import ExecutionContextModel, WorkflowModel
from flux.worker_registry import WorkerInfo


@pytest.fixture
def manager(tmp_path):
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = db_url
        mock_config.return_value.settings.database_type = "sqlite"
        mock_config.return_value.settings.security.auth.enabled = False
        yield DatabaseContextManager()


def _make_worker(name: str = "w-1") -> WorkerInfo:
    return WorkerInfo(name=name)


def _seed_paused_then_resuming(manager, exec_id: str = "exec-1") -> ExecutionContext:
    """Seed a workflow row + an execution that has been transitioned to RESUMING
    with a WORKFLOW_RESUMING event already appended."""
    with manager.session() as session:
        session.add(
            WorkflowModel(
                id="wf-1",
                namespace="default",
                name="test_wf",
                version=1,
                imports=[],
                source=b"",
            ),
        )
        session.commit()

    ctx: ExecutionContext = ExecutionContext(
        workflow_id="wf-1",
        workflow_name="test_wf",
        workflow_namespace="default",
        input=None,
        execution_id=exec_id,
    )
    ctx._state = ExecutionState.RESUMING
    ctx.events.append(
        ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_RESUMING,
            source_id="server",
            name="test_wf",
            value={"message": "hi"},
        ),
    )
    manager.save(ctx)
    return ctx


def test_next_resume_transitions_to_resume_scheduled(manager):
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")

    result = manager.next_resume(worker)

    assert result is not None
    assert result.state == ExecutionState.RESUME_SCHEDULED
    assert result.current_worker == "w-1"
    assert any(e.type == ExecutionEventType.WORKFLOW_RESUME_SCHEDULED for e in result.events)


def test_next_resume_is_idempotent_on_second_call(manager):
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")

    first = manager.next_resume(worker)
    second = manager.next_resume(worker)

    assert first is not None
    assert second is None


def test_next_resume_sticky_prefers_bound_worker(manager):
    ctx = _seed_paused_then_resuming(manager, "exec-1")
    with manager.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        model.worker_name = "w-1"
        session.commit()

    other_worker = _make_worker("w-2")
    result_other = manager.next_resume(other_worker)
    assert result_other is None, "w-2 must not pick up a w-1-bound execution via sticky"

    owner = _make_worker("w-1")
    result_owner = manager.next_resume(owner)
    assert result_owner is not None
    assert result_owner.current_worker == "w-1"


def test_next_resume_fallback_picks_up_null_worker(manager):
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-7")
    result = manager.next_resume(worker)
    assert result is not None
    assert result.current_worker == "w-7"


def test_claim_resume_transitions_resume_scheduled_to_resume_claimed(manager):
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")
    manager.next_resume(worker)  # moves row into RESUME_SCHEDULED

    ctx = manager.claim_resume("exec-1", worker)

    assert ctx.state == ExecutionState.RESUME_CLAIMED
    assert ctx.current_worker == "w-1"
    assert any(e.type == ExecutionEventType.WORKFLOW_RESUME_CLAIMED for e in ctx.events)


def test_claim_resume_on_already_claimed_raises(manager):
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")
    manager.next_resume(worker)
    manager.claim_resume("exec-1", worker)

    with pytest.raises(ExecutionError):
        manager.claim_resume("exec-1", worker)


def test_claim_resume_on_resuming_row_raises(manager):
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")
    # Skip next_resume — row is still RESUMING
    with pytest.raises(ExecutionError):
        manager.claim_resume("exec-1", worker)


def test_unclaim_from_resume_scheduled_recovers_to_resuming(manager):
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")
    manager.next_resume(worker)  # → RESUME_SCHEDULED

    ctx = manager.unclaim("exec-1")

    assert ctx.state == ExecutionState.RESUMING


def test_unclaim_from_resume_scheduled_clears_worker_name(manager):
    """Regression-pin: RESUME-recovery path must null worker_name so
    next_resume()'s fallback queue can pick up the execution on another
    worker after eviction/unclaim."""
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")
    manager.next_resume(worker)  # → RESUME_SCHEDULED, worker_name = "w-1"

    manager.unclaim("exec-1")

    with manager.session() as session:
        model = session.get(ExecutionContextModel, "exec-1")
        assert model.worker_name is None


def test_unclaim_from_resume_claimed_recovers_to_resuming(manager):
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")
    manager.next_resume(worker)
    manager.claim_resume("exec-1", worker)

    ctx = manager.unclaim("exec-1")

    assert ctx.state == ExecutionState.RESUMING


def test_unclaim_from_resume_claimed_clears_worker_name(manager):
    """Regression-pin: RESUME_CLAIMED → RESUMING must also null worker_name."""
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")
    manager.next_resume(worker)
    manager.claim_resume("exec-1", worker)

    manager.unclaim("exec-1")

    with manager.session() as session:
        model = session.get(ExecutionContextModel, "exec-1")
        assert model.worker_name is None


def test_unclaim_from_running_still_recovers_to_created(manager):
    """Regression-pin: initial-execution RUNNING crashes still go to CREATED."""
    with manager.session() as session:
        session.add(
            WorkflowModel(
                id="wf-2",
                namespace="default",
                name="test_wf_run",
                version=1,
                source=b"",
                imports=[],
            ),
        )
        session.commit()

    ctx = ExecutionContext(
        workflow_id="wf-2",
        workflow_name="test_wf_run",
        workflow_namespace="default",
        input=None,
        execution_id="exec-run",
    )
    ctx._state = ExecutionState.RUNNING
    manager.save(ctx)

    result = manager.unclaim("exec-run")
    assert result.state == ExecutionState.CREATED


def test_unclaim_from_running_clears_worker_name(manager):
    """Regression-pin: initial-recovery path clears worker_name so the row
    falls back to affinity-based dispatch on next_execution."""
    with manager.session() as session:
        session.add(
            WorkflowModel(
                id="wf-running",
                namespace="default",
                name="running_wf",
                version=1,
                source=b"",
                imports=[],
            ),
        )
        session.commit()

    ctx = ExecutionContext(
        workflow_id="wf-running",
        workflow_name="running_wf",
        workflow_namespace="default",
        input=None,
        execution_id="exec-run-clear",
    )
    ctx._state = ExecutionState.RUNNING
    ctx._current_worker = "w-1"
    manager.save(ctx)

    manager.unclaim("exec-run-clear")

    # Re-fetch from DB to verify worker_name was cleared
    with manager.session() as session:
        model = session.get(ExecutionContextModel, "exec-run-clear")
        assert model.worker_name is None, (
            f"worker_name should be cleared on initial-recovery; got {model.worker_name!r}"
        )


def test_find_by_worker_returns_active_executions_for_worker(manager):
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")
    manager.next_resume(worker)

    results = manager.find_by_worker("w-1")

    assert len(results) == 1
    assert results[0].execution_id == "exec-1"
    assert results[0].state == ExecutionState.RESUME_SCHEDULED


def test_find_by_worker_excludes_other_workers(manager):
    _seed_paused_then_resuming(manager, "exec-1")
    worker = _make_worker("w-1")
    manager.next_resume(worker)

    results = manager.find_by_worker("w-2")
    assert results == []


def test_find_by_worker_excludes_terminal_states(manager):
    with manager.session() as session:
        session.add(
            WorkflowModel(
                id="wf-terminal",
                namespace="default",
                name="terminal_wf",
                version=1,
                source=b"",
                imports=[],
            ),
        )
        session.commit()

    ctx = ExecutionContext(
        workflow_id="wf-terminal",
        workflow_name="terminal_wf",
        workflow_namespace="default",
        input=None,
        execution_id="exec-terminal",
    )
    ctx._state = ExecutionState.COMPLETED
    ctx._current_worker = "w-1"
    manager.save(ctx)

    results = manager.find_by_worker("w-1")
    assert len(results) == 0
