"""Tests for the resume-claim state machine additions to ExecutionContext."""

from __future__ import annotations

import pytest

from flux.domain.events import ExecutionEventType, ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.worker_registry import WorkerInfo


def test_resume_scheduled_state_exists():
    assert ExecutionState.RESUME_SCHEDULED.value == "RESUME_SCHEDULED"


def test_resume_claimed_state_exists():
    assert ExecutionState.RESUME_CLAIMED.value == "RESUME_CLAIMED"


def test_workflow_resume_scheduled_event_type_exists():
    assert ExecutionEventType.WORKFLOW_RESUME_SCHEDULED.value == "WORKFLOW_RESUME_SCHEDULED"


def test_workflow_resume_claimed_event_type_exists():
    assert ExecutionEventType.WORKFLOW_RESUME_CLAIMED.value == "WORKFLOW_RESUME_CLAIMED"


def _make_ctx(state: ExecutionState) -> ExecutionContext:
    ctx: ExecutionContext = ExecutionContext(
        workflow_id="wf-1",
        workflow_name="test_wf",
        workflow_namespace="default",
        input=None,
    )
    ctx._state = state
    return ctx


def _make_worker(name: str = "w-1") -> WorkerInfo:
    return WorkerInfo(name=name, runtime=None, resources=None, packages=[], labels={})


def test_resume_schedule_transitions_state_and_appends_event():
    ctx = _make_ctx(ExecutionState.RESUMING)
    worker = _make_worker("w-1")
    ctx.resume_schedule(worker)
    assert ctx.state == ExecutionState.RESUME_SCHEDULED
    assert ctx.current_worker == "w-1"
    assert ctx.events[-1].type == ExecutionEventType.WORKFLOW_RESUME_SCHEDULED
    assert ctx.events[-1].source_id == "w-1"


def test_resume_schedule_from_wrong_state_raises():
    ctx = _make_ctx(ExecutionState.PAUSED)
    worker = _make_worker("w-1")
    with pytest.raises(Exception):
        ctx.resume_schedule(worker)
