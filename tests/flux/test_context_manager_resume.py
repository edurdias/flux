"""Tests for the resume-claim additions in DatabaseContextManager."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from flux.context_managers import DatabaseContextManager
from flux.domain.events import ExecutionEvent, ExecutionEventType, ExecutionState
from flux.domain.execution_context import ExecutionContext
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
